# AI Recording Bean Flutter

Flutter 版智能录音豆控制端，同时面向 Android 和 iOS。迁移基准为原生 Android `0.3.11`。

## 已迁移

- BLE 扫描、手动选设备、上次设备标记、连接和断线重连；App 启动后可直接进入主界面，账户登录为可选。
- 录音开始/停止、设备校时、状态通知和上传进度。
- BLE 分页读取 TF 卡文件、批量选择和删除。
- 通过设备 Wi-Fi 下载 WAV，并使用系统文件/分享面板保存。
- 局域网 HTTP 状态、文件管理和 mDNS/Bonjour 发现。
- 转写状态轮询和 ASR 模型切换。
- 统一版本检查、App 更新入口和 ESP32 OTA 安装命令。
- “AI设置”页面：按录音豆设备 ID 保存文字优化开关、工作内容筛选开关、多选工作类型、自定义工作描述、排除内容和筛选程度。

## 开发环境

Flutter SDK 位于 `/Users/jh-05/development/flutter`，终端 PATH 已写入 `~/.zshrc`。

```bash
cd flutter_app
flutter pub get
flutter analyze
flutter test
flutter run
```

BLE、Bonjour、WAV 下载和 OTA 必须在 Android/iPhone 真机上联调。iOS 签名 Team 需要在 Xcode 中选择开发者账号。
