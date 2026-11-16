#!/usr/bin/env python3
"""Capture and summarize a production firmware boot log."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import subprocess
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


def reset_target(port: str) -> None:
    platformio_dir = Path.home() / ".platformio"
    python = platformio_dir / "penv" / "bin" / "python"
    esptool = platformio_dir / "packages" / "tool-esptoolpy" / "esptool.py"
    subprocess.run(
        [
            str(python),
            str(esptool),
            "--chip",
            "esp32s3",
            "--port",
            port,
            "--before",
            "default_reset",
            "--after",
            "hard_reset",
            "run",
        ],
        check=True,
    )


def production_image_hash() -> str:
    marker = PACKAGE_DIR / "release" / "LATEST"
    if not marker.is_file():
        return "未知"
    release = PACKAGE_DIR / "release" / marker.read_text(encoding="ascii").strip()
    sums = release / "SHA256SUMS.txt"
    if not sums.is_file():
        return "未知"
    for line in sums.read_text(encoding="ascii").splitlines():
        digest, name = line.split(None, 1)
        if name.strip() == "production/merged.bin":
            return digest
    return "未知"


def inspect_capture(captured: str, expected_device_id: str) -> dict[str, object]:
    identity_match = re.search(r"Firmware: ([^,\r\n]+), device ID: ([^\s\r\n]+)", captured)
    firmware = identity_match.group(1) if identity_match else "未捕获"
    device_id = identity_match.group(2) if identity_match else "未捕获"
    identity_format_ok = bool(identity_match) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", device_id))
    identity_ok = identity_format_ok and device_id == expected_device_id
    sd_ready = "microSD: ready" in captured
    microphone_ready = "microphone: ready" in captured
    ble_ready = "BLE control ready" in captured
    right_channel = "microphone PDM channel: right" in captured
    capture_ok = bool(captured)
    overall = (
        "PASS"
        if identity_ok and sd_ready and microphone_ready and ble_ready and right_channel
        else "采集失败"
        if not capture_ok
        else "FAIL"
    )
    return {
        "firmware": firmware,
        "device_id": device_id,
        "identity_format_ok": identity_format_ok,
        "identity_ok": identity_ok,
        "sd_ready": sd_ready,
        "microphone_ready": microphone_ready,
        "ble_ready": ble_ready,
        "right_channel": right_channel,
        "capture_ok": capture_ok,
        "overall": overall,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="采集正式固件启动日志")
    parser.add_argument("--port", help="串口，例如/dev/cu.usbmodem101")
    parser.add_argument("--board-id", help="扩展板编号")
    parser.add_argument("--operator", help="验收人；传空字符串表示未填写")
    parser.add_argument(
        "--expected-device-id",
        default="bean_ye_2",
        help="本次主板应保持的device_id；默认bean_ye_2",
    )
    parser.add_argument("--reset", action="store_true", help="采集前自动硬复位XIAO")
    parser.add_argument("--timeout", type=float, default=90, help="最长采集秒数")
    args = parser.parse_args()

    board_id = args.board_id or input("请输入扩展板编号：").strip()
    operator = args.operator.strip() if args.operator is not None else input("验收人（可留空）：").strip()
    port = detect_port(args.port)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}_{safe_name(board_id)}_production_boot"
    log_dir = PACKAGE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    raw_log = log_dir / f"{base}.log"
    report = log_dir / f"{base}.md"

    if args.reset:
        print("正在自动硬复位XIAO；随后会自动重连串口。")
        reset_target(port)
    else:
        print("已打开115200串口。若没有立即出现启动信息，请按一下XIAO Reset。")
    captured = ""
    serial_error = ""
    reconnects = 0
    device: serial.Serial | None = None
    try:
        with raw_log.open("w", encoding="utf-8") as log:
            started = time.monotonic()
            while time.monotonic() - started < args.timeout:
                if device is None:
                    try:
                        device = serial.Serial(port, 115200, timeout=0.2)
                    except serial.SerialException as error:
                        serial_error = str(error)
                        time.sleep(0.1)
                        continue
                try:
                    raw = device.read(device.in_waiting or 1)
                except (OSError, serial.SerialException) as error:
                    serial_error = str(error)
                    device.close()
                    device = None
                    reconnects += 1
                    time.sleep(0.1)
                    continue
                if not raw:
                    continue
                text = raw.decode("utf-8", errors="replace")
                captured += text
                log.write(text)
                log.flush()
                print(text, end="", flush=True)
                ready_line = "BLE control ready" in captured or "recovery hotspot is ready" in captured
                if ready_line and "microSD:" in captured and "microphone:" in captured:
                    break
    except KeyboardInterrupt as error:
        serial_error = str(error)
    finally:
        if device is not None:
            device.close()

    result = inspect_capture(captured, args.expected_device_id)
    firmware = str(result["firmware"])
    device_id = str(result["device_id"])
    identity_format_ok = bool(result["identity_format_ok"])
    identity_ok = bool(result["identity_ok"])
    sd_ready = bool(result["sd_ready"])
    microphone_ready = bool(result["microphone_ready"])
    ble_ready = bool(result["ble_ready"])
    right_channel = bool(result["right_channel"])
    capture_ok = bool(result["capture_ok"])
    overall = str(result["overall"])

    lines = [
        "# 正式固件启动记录",
        "",
        f"- 扩展板编号：{board_id or '未填写'}",
        f"- 验收人：{operator or '未填写'}",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 串口：`{port}`",
        f"- 正式合并固件SHA-256：`{production_image_hash()}`",
        f"- 固件版本：`{firmware}`",
        f"- device_id：`{device_id}`",
        f"- 预期device_id：`{args.expected_device_id}`",
        f"- 总体结果：**{overall}**",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        f"| 设备身份格式有效 | {'PASS' if identity_format_ok else 'FAIL'} |",
        f"| 主板身份保持为{args.expected_device_id} | {'PASS' if identity_ok else 'FAIL'} |",
        f"| microSD初始化 | {'PASS' if sd_ready else 'FAIL'} |",
        f"| 麦克风初始化 | {'PASS' if microphone_ready else 'FAIL'} |",
        f"| 新扩展板PDM右声道 | {'PASS' if right_channel else 'FAIL'} |",
        f"| BLE就绪 | {'PASS' if ble_ready else 'FAIL'} |",
        f"| USB复位后自动重连 | {'PASS' if reconnects > 0 else '未触发'}（{reconnects}次） |",
        "",
        f"原始日志：`{raw_log}`",
    ]
    if serial_error and not capture_ok:
        lines.extend(["", f"串口异常：`{serial_error}`"])
    lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n启动报告：{report}")
    print(f"总体结果：{overall}")
    return 0 if overall == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
