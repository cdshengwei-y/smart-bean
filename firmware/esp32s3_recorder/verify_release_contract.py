#!/usr/bin/env python3
"""Verify the custom-board production profile before packaging or flashing."""

from __future__ import annotations

import configparser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_ENV = "xiao_esp32s3_custom_board_release_038"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"RELEASE_CONTRACT_FAIL: {message}")


def main() -> int:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "platformio.ini", encoding="utf-8")

    require(parser.get("platformio", "default_envs").strip() == EXPECTED_ENV,
            f"default_envs必须为{EXPECTED_ENV}")
    section_name = f"env:{EXPECTED_ENV}"
    require(parser.has_section(section_name), f"缺少环境{EXPECTED_ENV}")
    flags = parser.get(section_name, "build_flags")
    for expected in (
        "RECORDER_CUSTOM_EXPANSION_BOARD=1",
        "RECORDER_SD_POWER_SWITCH=1",
        "RECORDER_FORCE_EFUSE_DEVICE_ID=1",
        "RECORDER_MIC_RIGHT_CHANNEL=1",
        'RECORDER_FIRMWARE_VERSION=\\"0.3.12\\"',
    ):
        require(expected in flags, f"正式环境缺少{expected}")
    require("RECORDER_DEVICE_ID" not in flags,
            "硬件发布环境不得把扩展板配置绑定到固定设备名")
    require("RECORDER_TEST_DEVICE_ID" not in flags,
            "通用正式环境不得启用测试设备身份")

    source = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
    for declaration in (
        "constexpr int kMicClockPin = 42;",
        "constexpr int kMicDataPin = 41;",
        "constexpr int kSdPowerEnPin = 40;",
        "constexpr int kSdSckPin = 7;",
        "constexpr int kSdMisoPin = 8;",
        "constexpr int kSdMosiPin = 9;",
    ):
        require(declaration in source, f"新扩展板引脚契约不匹配：{declaration}")
    # The CS pin is configurable for the legacy profiles, while the custom
    # expansion board keeps GPIO3 as the default and the production profile
    # does not override it.
    require(
        "#define RECORDER_SD_CS_PIN 3" in source
        and "constexpr int kSdCsPin = RECORDER_SD_CS_PIN;" in source,
        "新扩展板引脚契约不匹配：SD_CS默认必须为GPIO3",
    )
    for behavior in (
        "digitalWrite(kSdPowerEnPin, LOW);",
        "gpio_hold_en(static_cast<gpio_num_t>(kSdPowerEnPin));",
        "digitalWrite(kMicClockPin, LOW);",
        "gpio_deep_sleep_hold_en();",
        "deepSleepBlockerReason()",
        "logSleepBlocker(blocker);",
    ):
        require(behavior in source, f"低功耗关断契约缺失：{behavior}")

    identity = (ROOT / "src" / "device_identity.h").read_text(encoding="utf-8")
    require("0xF865DF90A994ULL" in identity and "kBeanYe2RecordedMac" in identity,
            "bean_ye_2的实测eFuse身份映射缺失")

    print(f"RELEASE_CONTRACT_PASS: {EXPECTED_ENV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
