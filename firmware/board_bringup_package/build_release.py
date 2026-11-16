#!/usr/bin/env python3
"""Build reproducible self-test and new-board production flash images."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime


PACKAGE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = PACKAGE_DIR.parents[1]
SELF_TEST_DIR = WORKSPACE_DIR / "firmware" / "esp32s3_hardware_self_test"
PRODUCTION_DIR = WORKSPACE_DIR / "firmware" / "esp32s3_recorder"
FLUTTER_DIR = WORKSPACE_DIR / "flutter_app"
DOCS_DIR = WORKSPACE_DIR / "docs" / "hardware"
SELF_TEST_ENV = "xiao_esp32s3_custom_expansion_self_test"
SAFE_PRODUCTION_ENV = "xiao_esp32s3_custom_board_release_038"
FORCE_SLEEP_ENV = "xiao_esp32s3_custom_board_force_sleep_test_038"
PRODUCTION_FIRMWARE_VERSION = "0.3.12"
FORCE_SLEEP_FIRMWARE_VERSION = "0.3.10-force-sleep20s-full-wake60s"


def find_pio() -> Path:
    command = shutil.which("pio") or shutil.which("platformio")
    if command:
        return Path(command)
    candidate = Path.home() / ".platformio" / "penv" / "bin" / "pio"
    if candidate.is_file():
        return candidate
    raise SystemExit("未找到PlatformIO。请先安装PlatformIO或确认~/.platformio存在。")


def find_flutter() -> Path:
    command = shutil.which("flutter")
    if command:
        return Path(command)
    candidate = Path.home() / "development" / "flutter" / "bin" / "flutter"
    if candidate.is_file():
        return candidate
    raise SystemExit("未找到Flutter。请确认flutter位于PATH或~/development/flutter/bin。")


def flutter_app_version() -> str:
    pubspec = (FLUTTER_DIR / "pubspec.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([^\s#]+)", pubspec, flags=re.MULTILINE)
    if not match:
        raise SystemExit("无法从flutter_app/pubspec.yaml读取版本号。")
    return match.group(1)


def run(
    command: list[str], cwd: Path | None = None, environment: dict[str, str] | None = None
) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_build_files(project: Path, environment: str, destination: Path) -> dict[str, Path]:
    build_dir = project / ".pio" / "build" / environment
    destination.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {}
    for name in ("bootloader.bin", "partitions.bin", "firmware.bin"):
        source = build_dir / name
        if not source.is_file():
            raise SystemExit(f"缺少构建产物：{source}")
        target = destination / name
        shutil.copy2(source, target)
        copied[name] = target
    return copied


def merge_image(files: dict[str, Path], output: Path) -> None:
    platformio_dir = Path.home() / ".platformio"
    python = platformio_dir / "penv" / "bin" / "python"
    esptool = platformio_dir / "packages" / "tool-esptoolpy" / "esptool.py"
    boot_app0 = (
        platformio_dir
        / "packages"
        / "framework-arduinoespressif32"
        / "tools"
        / "partitions"
        / "boot_app0.bin"
    )
    for required in (python, esptool, boot_app0):
        if not required.is_file():
            raise SystemExit(f"缺少合并固件所需文件：{required}")
    copied_boot_app0 = output.parent / "boot_app0.bin"
    shutil.copy2(boot_app0, copied_boot_app0)
    run(
        [
            str(python),
            str(esptool),
            "--chip",
            "esp32s3",
            "merge_bin",
            "-o",
            str(output),
            "0x0",
            str(files["bootloader.bin"]),
            "0x8000",
            str(files["partitions.bin"]),
            "0xe000",
            str(copied_boot_app0),
            "0x10000",
            str(files["firmware.bin"]),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成新扩展板到板烧录包")
    parser.add_argument(
        "--production-env",
        default=SAFE_PRODUCTION_ENV,
        help=f"正式固件环境；默认且推荐：{SAFE_PRODUCTION_ENV}",
    )
    parser.add_argument(
        "--allow-unsafe-environment",
        action="store_true",
        help="允许使用可能包含旧板固定device_id的其他环境",
    )
    args = parser.parse_args()

    if args.production_env != SAFE_PRODUCTION_ENV and not args.allow_unsafe_environment:
        raise SystemExit(
            "拒绝构建：该环境可能继承旧板固定device_id。"
            f"新板必须使用{SAFE_PRODUCTION_ENV}。"
        )
    if not (PRODUCTION_DIR / "src" / "device_config.h").is_file():
        raise SystemExit("缺少正式固件src/device_config.h，尚不能生成可联网正式固件。")

    pio = find_pio()
    flutter = find_flutter()
    run([sys.executable, str(PRODUCTION_DIR / "verify_release_contract.py")], cwd=PRODUCTION_DIR)
    run(
        [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
        cwd=PACKAGE_DIR,
    )
    run([str(pio), "run", "-e", SELF_TEST_ENV], cwd=SELF_TEST_DIR)
    run([str(pio), "run", "-e", args.production_env], cwd=PRODUCTION_DIR)
    run([str(pio), "run", "-e", FORCE_SLEEP_ENV], cwd=PRODUCTION_DIR)
    run([str(flutter), "pub", "get"], cwd=FLUTTER_DIR)
    run([str(flutter), "analyze"], cwd=FLUTTER_DIR)
    run([str(flutter), "test"], cwd=FLUTTER_DIR)
    run([str(flutter), "build", "apk", "--debug"], cwd=FLUTTER_DIR)

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output_dir = PACKAGE_DIR / "release" / stamp
    self_test_output = output_dir / "self_test"
    production_output = output_dir / "production"
    force_sleep_output = output_dir / "force_sleep"
    self_test_files = copy_build_files(SELF_TEST_DIR, SELF_TEST_ENV, self_test_output)
    production_files = copy_build_files(PRODUCTION_DIR, args.production_env, production_output)
    force_sleep_files = copy_build_files(PRODUCTION_DIR, FORCE_SLEEP_ENV, force_sleep_output)
    merge_image(self_test_files, self_test_output / "merged.bin")
    merge_image(production_files, production_output / "merged.bin")
    merge_image(force_sleep_files, force_sleep_output / "merged.bin")

    mobile_output = output_dir / "android"
    mobile_output.mkdir(parents=True, exist_ok=True)
    mobile_apk = FLUTTER_DIR / "build" / "app" / "outputs" / "flutter-apk" / "app-debug.apk"
    if not mobile_apk.is_file():
        raise SystemExit("Flutter构建成功但缺少Debug APK。")
    mobile_version = flutter_app_version()
    packaged_apk = mobile_output / f"AI-Recording-Bean-{mobile_version}-debug.apk"
    shutil.copy2(mobile_apk, packaged_apk)

    for document in (
        "board_arrival_acceptance_record.md",
        "production_firmware_acceptance_flow.md",
    ):
        shutil.copy2(DOCS_DIR / document, output_dir / document)

    release_files = sorted((*output_dir.rglob("*.bin"), *output_dir.rglob("*.apk")))
    sums = []
    manifest_files = []
    for path in release_files:
        relative = path.relative_to(output_dir).as_posix()
        digest = sha256(path)
        sums.append(f"{digest}  {relative}")
        manifest_files.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest})
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="ascii")

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "board": "Seeed Studio XIAO ESP32S3",
        "self_test_environment": SELF_TEST_ENV,
        "production_environment": args.production_env,
        "production_firmware_version": PRODUCTION_FIRMWARE_VERSION,
        "force_sleep_environment": FORCE_SLEEP_ENV,
        "force_sleep_firmware_version": FORCE_SLEEP_FIRMWARE_VERSION,
        "force_sleep_warning": "measurement only; restore production image after testing",
        "production_identity_policy": "bean_ye_2 main-board identity retained across expansion-board replacement",
        "expected_device_id": "bean_ye_2",
        "mobile_app_framework": "Flutter",
        "android_app_version": mobile_version,
        "android_app_variant": "debug",
        "merged_image_flash_offset": "0x0",
        "contains_local_network_credentials": True,
        "files": manifest_files,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PACKAGE_DIR / "release" / "LATEST").write_text(stamp + "\n", encoding="ascii")

    print(f"\n烧录包已生成：{output_dir}")
    print("警告：production/merged.bin包含本机device_config.h中的联网凭据，不要公开分发。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
