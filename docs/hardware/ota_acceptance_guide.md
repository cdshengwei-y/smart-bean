# OTA 局域网验收记录

更新日期：2026-08-28

## 目的

这份记录只用于验证“手机 App 通过 BLE 发起 OTA，录音豆通过局域网下载固件并重启”的链路。
它使用本地 HTTP 服务，不能作为正式公网升级方案，也不能把测试固件当作正式交付固件。

## 当前版本边界

| 项目 | 版本 | 状态 |
| --- | --- | --- |
| 设备当前正式固件 | `0.3.10` | 已有功能闭环验收 |
| OTA 验收固件 | `0.3.13-ota-acceptance` | Android 真机已观察到设备升级到该版本；升级后业务复测受 TF 卡状态影响，暂不写全通过 |
| 手机 App | Flutter `0.5.32+56` | Android 更新页卡住问题已修复并重装复测；iPhone OTA 仍需单独验收 |
| OTA 服务 | 本地 `backend/ota_server.py` 或后台 OTA 服务 | 局域网接口和固件下载请求已验证 |

## 电脑端准备

在项目根目录执行：

```bash
cd firmware/esp32s3_recorder
~/.platformio/penv/bin/pio run \
  -e xiao_esp32s3_custom_board_ota_acceptance_0311
```

构建产物为：

```text
firmware/esp32s3_recorder/.pio/build/xiao_esp32s3_custom_board_ota_acceptance_0311/firmware.bin
```

启动局域网 OTA 服务：

```bash
PYTHONPATH=backend python3 backend/ota_server.py \
  --firmware firmware/esp32s3_recorder/.pio/build/xiao_esp32s3_custom_board_ota_acceptance_0311/firmware.bin \
  --apk flutter_app/build/app/outputs/flutter-apk/app-release.apk \
  --version 0.3.11 \
  --app-version 0.5.32+56 \
  --public-base-url http://192.168.0.107:8790
```

服务启动后，电脑和录音豆必须在同一个局域网。先在电脑浏览器打开：

```text
http://127.0.0.1:8790/health
http://127.0.0.1:8790/manifest.json
http://127.0.0.1:8790/release.json
```

确认返回的固件版本为本次 OTA 验收目标版本，且固件地址可以下载。

## 真机验收步骤

1. 确认录音豆当前 BLE 状态中的 `firmware=0.3.10`。
2. 确认录音豆能连接配置的 Wi-Fi，且该 Wi-Fi 能访问电脑的 `192.168.0.107:8790`。
3. 手机 App 打开更新页面，点击检查更新。
4. 确认显示当前固件 `0.3.10`、可用固件 `0.3.11`。
5. 开始 OTA，等待设备断开 BLE 并自动重启。
6. 重新扫描并连接录音豆，确认 BLE 状态中的 `firmware=0.3.11`。
7. 重新测试开始录音、停止录音、TF 文件列表和上传，确认 OTA 后业务功能仍正常。
8. 在 OTA 前后各保留一个测试录音，确认原有 TF 文件没有消失。

以上 1-8 步全部通过后，才把“OTA 正常升级”记为通过。

## 失败与恢复验收

失败测试只能使用测试固件和可恢复环境：

- 服务不可访问：App/设备应显示失败，设备不能因此丢失原有录音。
- manifest 不是更高版本：设备状态应为 `up_to_date`，不应重启。
- 固件地址不存在或下载中断：设备应回报 `update_failed_*`，原固件仍可启动。
- OTA 过程中断电：重新上电后确认原固件或可启动 OTA 分区仍能工作，再刷回正式固件。

“失败后恢复正常”必须以真机串口、BLE 状态和录音功能共同确认，电脑端单测不能代替。

## 恢复正式固件

OTA 验收结束后，必须确认设备最终使用正式环境：

```text
xiao_esp32s3_custom_board_release_038
```

正式固件版本应为：

```text
0.3.10
```

如果测试固件已经成功升级到 `0.3.11`，恢复正式固件使用到板烧录包的 production 镜像，
不要把 `0.3.11` 测试镜像写进正式版本矩阵。

## 当前结论

- OTA 固件代码、App 命令、版本比较和本地服务接口已经实现。
- 本地 OTA 服务单元测试已通过。
- 2026-08-27，Android App 已通过 BLE 连接 `bean_ye_1`，更新页读取到当前固件 `0.3.13-ota-acceptance`、设备状态 `up_to_date`，并且不再无限转圈。
- Android OTA 后录音、TF、上传、Obsidian 业务复测：当前受 TF 卡状态影响，后续换卡后再验收。
- Android OTA 失败恢复：**已通过**。2026-08-28 使用无效测试固件触发
  `update_failed_-106`，设备仍可连接和正常使用，用户已确认验收通过。
- Android OTA 正常升级：**已通过**。2026-08-28，设备从 `0.3.12` 通过 App 发起局域网 OTA，重启后版本变为 `0.3.13-ota-acceptance`，重新检查显示“已是最新版本”。
- OTA 后业务复测和 iPhone OTA 真机验收：**待验收**。
- Android 和 iPhone 的 OTA 真机验收分别记录，不能因为其中一端通过就视为另一端通过。
