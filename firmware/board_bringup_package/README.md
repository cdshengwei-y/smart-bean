# 新扩展板到板烧录包

用于构建、校验、烧录和记录新扩展板的自检与正式固件。适用主控为Seeed Studio XIAO ESP32S3。

## 最简单的使用方式

在macOS Finder中双击`board_bringup.command`：

1. 板子到之前先选`1`，构建最新烧录包。
2. XIAO不要连接扩展板，选`2`烧录自检固件。
3. 烧录完成后完全断电，再插合扩展板和FAT32 TF卡。
4. 重新运行菜单选`3`，按终端提示观察LED、讲话和测量TF电压。
5. 自检报告为PASS后，选`4`烧录正式固件。
6. 重新运行菜单选`5`，按提示采集正式固件版本、device_id和外设启动结果。
7. 按`docs/hardware/production_firmware_acceptance_flow.md`继续正式验收。

烧录和采集故意分成两个菜单项，确保连接器只在完全断电时插合。

## 主板身份与扩展板配置

正式包固定使用PlatformIO环境：

```text
xiao_esp32s3_custom_board_release_038
```

当前只是把`bean_ye_2`的官方扩展板换成自研扩展板，XIAO主板未更换，因此业务身份仍为`bean_ye_2`。固件同时兼容ESP32 eFuse MAC的两种字节顺序，避免再次错误生成`bean-F865DF90A994`目录；同一环境烧到真正的新XIAO时会生成该主板自己的eFuse身份，不会冒用`bean_ye_2`。

烧录正式固件后必须从115200串口记录启动行中的`Firmware`和`device ID`，并核对MinIO路径为`recordings/<device_id>/`。

### 2026-10-26：按现有 Obsidian 绑定测试 bean_ye_2

本次连接主板的实测 MAC 为 `68:ee:8f:47:21:dc`，通用固件会自动识别为 `bean_ye_1`。用户明确要求匹配当前 Obsidian 的 `bean_ye_2` 绑定，因此新增独立环境：

```text
xiao_esp32s3_custom_board_bean_ye_2_binding_test
```

该环境使用新扩展板硬件配置，以 `RECORDER_TEST_DEVICE_ID` 显式指定本次测试身份，版本为 `0.3.12-bean-ye-2-binding-test`。新录音使用 `recordings/bean_ye_2/`，对应笔记使用 `notes/bean_ye_2/`。通用正式环境仍按主板 MAC 识别身份，刷回通用包后这块主板会再次上报 `bean_ye_1`。

本次独立烧录包位于 `release/20261026-153524-bean-ye-2-binding-test/`，含构建产物、SHA-256、清单、写入日志与启动校验结果。实际写入应用和 OTA 引导数据，分区布局已核对一致。启动实测 `device ID: bean_ye_2`，TF 卡、麦克风、右声道和 BLE 检查均通过，详见包内 `boot-reset-verification.json`。

## 生成内容

`build_release.py`每次生成独立时间戳目录：

```text
release/YYYYMMDD-HHMMSS/
  self_test/merged.bin
  production/merged.bin
  force_sleep/merged.bin
  android/AI-Recording-Bean-<Flutter版本>-debug.apk
  SHA256SUMS.txt
  manifest.json
  board_arrival_acceptance_record.md
  production_firmware_acceptance_flow.md
```

三个`merged.bin`都从地址`0x0`烧录，内含bootloader、分区表、OTA初始数据和应用固件。`flash_release.py`会先校验SHA-256再调用esptool。构建过程会校验新板固件契约，运行Flutter静态分析和测试，并生成当前Flutter控制端的Debug APK。

测深睡电流时，直接烧录`force_sleep/merged.bin`。该镜像上电5秒后强制休眠，仅用于测量；完成后必须重新烧录正式环境。

## 命令行用法

```bash
cd firmware/board_bringup_package
$HOME/.platformio/penv/bin/python build_release.py
$HOME/.platformio/penv/bin/python flash_release.py self-test
$HOME/.platformio/penv/bin/python run_self_test.py
$HOME/.platformio/penv/bin/python flash_release.py production
$HOME/.platformio/penv/bin/python capture_production_boot.py
```

深睡测量使用：

```bash
$HOME/.platformio/penv/bin/python flash_release.py force-sleep
# 测量完成后恢复正式固件
$HOME/.platformio/penv/bin/python flash_release.py production
```

连接多个串口设备时用`--port`明确指定XIAO，例如：

```bash
$HOME/.platformio/penv/bin/python flash_release.py self-test --port /dev/cu.usbmodem101
$HOME/.platformio/penv/bin/python run_self_test.py --port /dev/cu.usbmodem101 --board-id PCB-001
```

## 输出和安全

- 自动自检报告和原始串口日志保存在`logs/`。
- `release/`和`logs/`默认不进入版本控制。
- `production/merged.bin`包含本机`device_config.h`编译进去的Wi-Fi和MinIO凭据，只能在可信设备和本机保存，不能公开发送。
- 脚本不会自动擦除整片Flash，也不会修改`device_config.h`。
- 任何短路、异常发热、异味或反复复位都应立即断电，不继续自动测试。
