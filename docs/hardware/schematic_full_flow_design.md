# AI录音豆新扩展板硬件与连接总说明

更新日期：2026-08-04  
状态：PCB已投产，本文作为到板验收和固件联调的当前权威说明。

## 1. 设计范围

本项目只设计接在Seeed Studio XIAO ESP32S3上的单块扩展板：

- 删除官方Sense扩展板的摄像头、摄像头FPC和相关电源电路。
- 保留并重新实现PDM麦克风和microSD卡功能。
- 新增TPS22919高边负载开关，由GPIO40控制TF卡电源。
- 保持TF卡座、板对板连接器和麦克风的关键机械位置与官方底板兼容。
- 正式录音由手机App通过BLE开始/停止，不设置扩展板物理录音按键。
- XIAO的GPIO0只用于BOOT/下载；GPIO1在当前正式固件中不用。

## 2. 连接器编号原则

连接器脚号、PCB封装Pad号、XIAO J3 Pad号和MCU GPIO号是不同编号体系，不能按数字大小直接对应。必须按板对板连接器实体接触位置核对。

特别需要固定记住：

```text
新板 CN1 Pad28
-> 官方扩展板 JA3 Pin29 的实体位置
-> XIAO J3 Pad2
-> D2 / GPIO3
-> SD_CS

新板 CN1 Pad1
-> 官方扩展板 JA3 Pin15 的实体位置
-> XIAO J3 Pad16
-> GPIO21 / USER_LED
-> 扩展板侧 NC
```

`CN1 Pad1 = NC`只表示新扩展板没有使用GPIO21，不会断开XIAO主板内部的USER LED。正式固件和自检固件仍可直接控制XIAO板载LED。

## 3. 当前权威引脚映射

| 新扩展板连接位置 | XIAO / MCU信号 | 当前用途 |
| --- | --- | --- |
| CN1 Pad1 | GPIO21 / USER_LED对应位置 | 扩展板侧NC |
| CN1 Pad19 | GPIO40 | SD_PWR_EN，控制TPS22919 ON |
| CN1 Pad21 | GPIO41 | PDM麦克风DATA |
| CN1 Pad23 | GPIO42 | PDM麦克风CLK |
| CN1 Pad22 | GPIO9 | SD_MOSI |
| CN1 Pad24 | GPIO8 | SD_MISO |
| CN1 Pad26 | GPIO7 | SD_SCK |
| CN1 Pad28 | D2 / GPIO3 | SD_CS |

正式固件、自检固件、验收记录和BOM连接摘要均必须使用这套映射。

## 4. TF卡电源链路

TF卡由独立3.3V支路供电，麦克风不跟随TF电源开关：

```text
XIAO 3V3 / 3V3_IN
        |
        +----> 麦克风VDD（常供电）
        |
        +----> TPS22919 IN
                 ON  <---- GPIO40 / SD_PWR_EN
                 OUT ----> L1 ----> VCC_SD_3V3 ----> TF卡及其上拉/ESD/电容
                 QOD ----> OUT
                 GND ----> GND
```

关键要求：

- TPS22919 ON为高电平有效。
- ON必须有100kΩ下拉，保证复位和深睡时TF默认断电。
- QOD接OUT，关闭时帮助释放输出电荷。
- TPS22919 NC保持悬空。
- 输入去耦电容靠近TPS22919 IN/GND。
- TF侧去耦电容靠近卡座VDD/GND。
- L1串在TPS22919 OUT与VCC_SD_3V3之间。

## 5. TF卡SPI链路

| 网络 | 连接器/MCU | 典型链路 |
| --- | --- | --- |
| SD_SCK | CN1 Pad26 / GPIO7 | GPIO7 → 22Ω串阻 → TF CLK；卡座侧ESD |
| SD_MISO | CN1 Pad24 / GPIO8 | TF DAT0 → 22Ω串阻 → GPIO8；TF侧上拉和ESD |
| SD_MOSI | CN1 Pad22 / GPIO9 | GPIO9 → 22Ω串阻 → TF CMD；TF侧上拉和ESD |
| SD_CS | CN1 Pad28 / D2/GPIO3 | GPIO3 → 0Ω串联位 → TF DAT3/CS；TF侧上拉和ESD |

断电前固件必须按以下顺序处理：

1. 停止所有TF读写并关闭文件。
2. 调用`SD.end()`并停止SPI总线。
3. 将CS、SCK、MISO和MOSI全部设为输入/高阻，防止信号脚反向供电。
4. GPIO40拉低，关闭TPS22919。
5. 深睡时保持GPIO40为低电平。

重新使用TF卡时：释放GPIO保持、GPIO40拉高、等待至少10ms、启动SPI并挂载；挂载失败时执行完整断电重试。

## 6. PDM麦克风链路

| 信号 | 连接器/MCU | 连接 |
| --- | --- | --- |
| MIC_CLK | CN1 Pad23 / GPIO42 | GPIO42 → T3902 CLK |
| MIC_DATA | CN1 Pad21 / GPIO41 | T3902 DATA → GPIO41 |
| MIC_SELECT | - | 按当前原理图接GND选择固定声道 |
| MIC_VDD | 3V3_IN | 常供电，不接VCC_SD_3V3 |

麦克风为底部进音器件，PCB声孔、焊锡、助焊剂、胶和外壳均不能遮挡进音孔。自检固件的PASS只证明PDM有活动数据，最终必须使用正式固件录音并试听。

## 7. XIAO板载USER LED

- USER LED在XIAO主板内部连接GPIO21。
- 新扩展板CN1 Pad1为NC，因此扩展板不具备GPIO21扩展能力。
- 这不会损失TF、麦克风、电源管理、BLE、Wi-Fi或录音功能。
- 自检固件会让GPIO21板载LED闪烁3次，需人工确认。

## 8. 正式固件硬件基线

```text
MIC_CLK      GPIO42
MIC_DATA     GPIO41
SD_PWR_EN    GPIO40
SD_CS        GPIO3
SD_SCK       GPIO7
SD_MISO      GPIO8
SD_MOSI      GPIO9
USER_LED     GPIO21（XIAO内部）
BOOT         GPIO0（只用于下载/复位操作）
GPIO1        未使用
```

正式固件工程：`firmware/esp32s3_recorder/`。  
到板自检工程：`firmware/esp32s3_hardware_self_test/`。

## 9. PCB和装配关注点

- CN1封装方向、Pad1标识和插合方向必须与XIAO实体一致。
- 不以顶层透视图中的视觉左右关系推断底层脚号。
- TF卡座贴板边并保留插拔行程和外壳开口。
- 麦克风位置、朝向和声孔与机械设计一致。
- TPS22919、L1和输入/输出电容靠近并缩短电源回路。
- TF SPI串阻靠近信号源布置，ESD靠近卡座布置。
- 3V3_IN、VCC_SD_3V3和GND保留可测位置。
- 天线区域、USB插拔空间和板对板连接器高度在外壳中保留净空。

## 10. 到板前已完成项

- 原理图、PCB、BOM和布局资料已完成，PCB已投产。
- SD_CS已在正式固件和自检固件中统一为GPIO3。
- SD_PWR_EN已统一为GPIO40。
- 自检固件支持TF读写、50次断电重挂载、麦克风活动、USER LED和TF电压测量。
- 到板烧录包可构建、校验、烧录并保存自检和正式启动日志。
- 到板验收记录和正式固件验收流程已完成。

## 11. 到板后必须实测

- 未上电短路、器件方向、虚焊、连锡和连接器插合。
- GPIO40关闭/开启时VCC_SD_3V3的实际电压。
- TF 64KiB读写校验及50/50断电重挂载。
- PDM麦克风活动及正式WAV试听。
- BLE开始/停止、文件操作、离线录音、恢复上传和转写同步。
- 录音中整机断电后的`.wav.part`恢复。
- 至少30分钟连续录音。
- 默认约2分钟空闲深睡、TF断电和整机深睡电流。

到板执行顺序见：

- `docs/hardware/board_arrival_acceptance_record.md`
- `docs/hardware/production_firmware_acceptance_flow.md`
- `firmware/board_bringup_package/README.md`
