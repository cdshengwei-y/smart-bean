#!/usr/bin/env python3
"""Flash a verified merged image from the latest board bring-up release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from serial.tools import list_ports


PACKAGE_DIR = Path(__file__).resolve().parent
EXPECTED_DEVICE_ID = "bean_ye_2"
EXPECTED_ENVIRONMENTS = {
    "self-test": ("self_test_environment", "xiao_esp32s3_custom_expansion_self_test"),
    "production": ("production_environment", "xiao_esp32s3_custom_board_release_038"),
    "force-sleep": (
        "force_sleep_environment",
        "xiao_esp32s3_custom_board_force_sleep_test_038",
    ),
}


def latest_release() -> Path:
    marker = PACKAGE_DIR / "release" / "LATEST"
    if not marker.is_file():
        raise SystemExit("尚未生成烧录包，请先运行build_release.py。")
    release = PACKAGE_DIR / "release" / marker.read_text(encoding="ascii").strip()
    if not release.is_dir():
        raise SystemExit(f"LATEST指向的目录不存在：{release}")
    return release


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
    raise SystemExit(
        "无法唯一确定XIAO串口，请只连接一块板或使用--port指定。\n当前串口：\n" + available
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_hash(release: Path, image: Path) -> str:
    relative = image.relative_to(release).as_posix()
    for line in (release / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, name = line.split(None, 1)
        if name.strip() == relative:
            return digest
    raise SystemExit(f"SHA256SUMS.txt中没有找到{relative}")


def verify_release_policy(release: Path, image_type: str) -> None:
    manifest_path = release / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"无法读取发布清单：{manifest_path}") from error

    environment_field, expected_environment = EXPECTED_ENVIRONMENTS[image_type]
    actual_environment = manifest.get(environment_field)
    if actual_environment != expected_environment:
        raise SystemExit(
            f"发布清单环境不匹配：{environment_field}={actual_environment!r}，"
            f"期望{expected_environment!r}"
        )
    if image_type != "self-test" and manifest.get("expected_device_id") != EXPECTED_DEVICE_ID:
        raise SystemExit(
            "发布清单设备身份不匹配："
            f"期望expected_device_id={EXPECTED_DEVICE_ID}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="烧录新扩展板合并固件")
    parser.add_argument("image", choices=("self-test", "production", "force-sleep"))
    parser.add_argument("--port", help="串口，例如/dev/cu.usbmodem101")
    parser.add_argument("--baud", default="115200")
    parser.add_argument("--yes", action="store_true", help="跳过烧录确认")
    args = parser.parse_args()

    release = latest_release()
    verify_release_policy(release, args.image)
    subdirectories = {
        "self-test": "self_test",
        "production": "production",
        "force-sleep": "force_sleep",
    }
    subdirectory = subdirectories[args.image]
    image = release / subdirectory / "merged.bin"
    if not image.is_file():
        raise SystemExit(f"缺少合并固件：{image}")
    actual = sha256(image)
    expected = expected_hash(release, image)
    if actual != expected:
        raise SystemExit("固件SHA-256校验失败，拒绝烧录。")

    port = detect_port(args.port)
    print(f"固件：{image}")
    print(f"串口：{port}")
    print(f"SHA-256：{actual}")
    if not args.yes:
        answer = input("确认烧录到当前XIAO？输入YES继续：").strip()
        if answer != "YES":
            print("已取消。")
            return 1

    platformio_dir = Path.home() / ".platformio"
    python = platformio_dir / "penv" / "bin" / "python"
    esptool = platformio_dir / "packages" / "tool-esptoolpy" / "esptool.py"
    command = [
        str(python),
        str(esptool),
        "--chip",
        "esp32s3",
        "--port",
        port,
        "--baud",
        args.baud,
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "write_flash",
        "-z",
        "--flash_size",
        "detect",
        "0x0",
        str(image),
    ]
    subprocess.run(command, check=True)
    suffixes = {
        "self-test": "下一步运行到板自检记录。",
        "production": "请打开115200串口核对Firmware和device ID。",
        "force-sleep": "仅用于测量；完成后必须重新烧录production正式固件。",
    }
    suffix = suffixes[args.image]
    print("烧录完成。" + suffix)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
