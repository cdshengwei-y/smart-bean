#!/usr/bin/env python3
"""Run the interactive board self-test and save raw and Markdown reports."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import time

import serial
from serial.tools import list_ports


PACKAGE_DIR = Path(__file__).resolve().parent


def detect_port(requested: str | None) -> str:
    if requested:
        return requested
    ports = list(list_ports.comports())
    candidates = [
        port.device
        for port in ports
        if port.vid is not None
        or "usbmodem" in port.device.lower()
        or "usbserial" in port.device.lower()
    ]
    if len(candidates) == 1:
        return candidates[0]
    available = "\n".join(f"  {port.device}: {port.description}" for port in ports) or "  无"
    raise SystemExit("无法唯一确定XIAO串口，请使用--port指定。\n当前串口：\n" + available)


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._-") or "unlabeled_board"


def ask_result(prompt: str) -> bool | None:
    while True:
        value = input(prompt + " [y/n/留空=未测]：").strip().lower()
        if not value:
            return None
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False


def status_text(value: bool | None) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "未测"


def self_test_image_hash() -> str:
    marker = PACKAGE_DIR / "release" / "LATEST"
    if not marker.is_file():
        return "未知"
    release = PACKAGE_DIR / "release" / marker.read_text(encoding="ascii").strip()
    sums = release / "SHA256SUMS.txt"
    if not sums.is_file():
        return "未知"
    for line in sums.read_text(encoding="ascii").splitlines():
        digest, name = line.split(None, 1)
        if name.strip() == "self_test/merged.bin":
            return digest
    return "未知"


class Session:
    def __init__(self, port: str, log_path: Path) -> None:
        self.serial = serial.Serial(port, 115200, timeout=0.2)
        self.log = log_path.open("w", encoding="utf-8")

    def close(self) -> None:
        self.log.close()
        self.serial.close()

    def send(self, command: str) -> None:
        self.log.write(f"\n>>> COMMAND {command}\n")
        self.log.flush()
        self.serial.write(command.encode("ascii") + b"\n")
        self.serial.flush()

    def wait_for(self, patterns: tuple[str, ...], timeout: float) -> str:
        started = time.monotonic()
        captured = ""
        while time.monotonic() - started < timeout:
            raw = self.serial.read(self.serial.in_waiting or 1)
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace")
            captured += text
            self.log.write(text)
            self.log.flush()
            print(text, end="", flush=True)
            if any(pattern in captured for pattern in patterns):
                return captured
        raise TimeoutError(f"等待串口输出超时：{patterns}")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行并保存新扩展板自检记录")
    parser.add_argument("--port", help="串口，例如/dev/cu.usbmodem101")
    parser.add_argument("--board-id", help="扩展板编号")
    args = parser.parse_args()

    board_id = args.board_id or input("请输入扩展板编号：").strip()
    operator = input("验收人（可留空）：").strip()
    port = detect_port(args.port)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}_{safe_name(board_id)}"
    log_dir = PACKAGE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_log = log_dir / f"{base}.log"
    report = log_dir / f"{base}.md"

    results: dict[str, bool | None] = {
        "automatic": None,
        "cycles": None,
        "microphone": None,
        "led": None,
        "sd_off_voltage": None,
        "sd_on_voltage": None,
    }
    off_voltage = ""
    on_voltage = ""
    microphone_diagnostic: bool | None = None
    session = Session(port, raw_log)
    try:
        session.serial.reset_input_buffer()
        print("等待自检固件命令接口；若刚上电，先让自动测试运行完。")
        session.send("?")
        session.wait_for(("Commands:",), 120)

        print("\n准备运行A：先观察XIAO板载LED闪烁；出现麦克风提示后讲话或轻敲。")
        session.serial.reset_input_buffer()
        session.send("A")
        automatic_output = session.wait_for(
            ("AUTOMATIC TESTS PASSED", "ONE OR MORE TESTS FAILED"), 180
        )
        results["automatic"] = "AUTOMATIC TESTS PASSED" in automatic_output

        print("\n单独复测XIAO板载LED。")
        session.serial.reset_input_buffer()
        session.send("L")
        session.wait_for(("[MANUAL] XIAO USER LED",), 20)
        results["led"] = ask_result("是否看到XIAO板载LED闪烁3次")

        print("\n运行50次TF断电重挂载。")
        session.serial.reset_input_buffer()
        session.send("C")
        cycle_output = session.wait_for(("SD repeated power cycling",), 240)
        results["cycles"] = (
            "[PASS] SD repeated power cycling" in cycle_output and "50/50" in cycle_output
        )

        print("\n单独复测麦克风，请持续讲话或轻敲麦克风附近3秒。")
        session.serial.reset_input_buffer()
        session.send("M")
        mic_output = session.wait_for(("Microphone electrical activity",), 30)
        results["microphone"] = "[PASS] Microphone electrical activity" in mic_output
        if results["microphone"] is False:
            print("\n麦克风活动失败，自动运行CLK/DATA数字接口诊断。")
            session.serial.reset_input_buffer()
            session.send("D")
            diagnostic_output = session.wait_for(("Microphone diagnostic complete.",), 30)
            microphone_diagnostic = (
                "[PASS] Independent diagnostic clock generator" in diagnostic_output
                and "[PASS] Microphone DATA response to diagnostic clock" in diagnostic_output
                and "[PASS] Microphone DATA idle short check" in diagnostic_output
            )

        session.serial.reset_input_buffer()
        session.send("0")
        session.wait_for(("TF power OFF.",), 10)
        off_voltage = input("测量VCC_SD_3V3关断电压并输入数值（V，可留空）：").strip()
        results["sd_off_voltage"] = ask_result("关断电压是否接近0V") if off_voltage else None

        session.serial.reset_input_buffer()
        session.send("1")
        session.wait_for(("TF power ON",), 10)
        on_voltage = input("测量VCC_SD_3V3开启电压并输入数值（V，可留空）：").strip()
        results["sd_on_voltage"] = ask_result("开启电压是否接近3.3V") if on_voltage else None
    except (KeyboardInterrupt, TimeoutError, serial.SerialException) as error:
        print(f"\n自检中止：{error}")
    finally:
        try:
            session.send("0")
            time.sleep(0.2)
        except serial.SerialException:
            pass
        session.close()

    values = list(results.values())
    if any(value is False for value in values):
        overall = "FAIL"
    elif all(value is True for value in values):
        overall = "PASS"
    else:
        overall = "未完成"

    rows = [
        ("A全部自动测试", results["automatic"], ""),
        ("XIAO板载LED", results["led"], "人工观察"),
        ("50次TF断电重挂载", results["cycles"], "目标50/50"),
        ("M麦克风活动", results["microphone"], ""),
        ("D麦克风数字接口", microphone_diagnostic,
         "仅在M失败时自动运行" if microphone_diagnostic is not None else "未触发"),
        ("TF关断电压", results["sd_off_voltage"], off_voltage + " V" if off_voltage else "未填写"),
        ("TF开启电压", results["sd_on_voltage"], on_voltage + " V" if on_voltage else "未填写"),
    ]
    lines = [
        "# 新扩展板自检结果",
        "",
        f"- 扩展板编号：{board_id or '未填写'}",
        f"- 验收人：{operator or '未填写'}",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 串口：`{port}`",
        f"- 自检合并固件SHA-256：`{self_test_image_hash()}`",
        f"- 总体结果：**{overall}**",
        f"- 原始日志：`{raw_log}`",
        "",
        "| 项目 | 结果 | 记录 |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {name} | {status_text(value)} | {detail} |" for name, value, detail in rows)
    lines.extend(["", "总体为“未完成”时，补齐电压或人工观察项目后才能签署到板验收。", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n自检报告：{report}")
    print(f"原始日志：{raw_log}")
    print(f"总体结果：{overall}")
    return 0 if overall == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
