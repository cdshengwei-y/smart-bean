# 新扩展板到板自检固件

用于首块新扩展板焊接完成后的独立硬件检查，不包含正式录音、BLE、Wi-Fi或上传功能。

推荐使用`firmware/board_bringup_package/board_bringup.command`完成构建、校验、烧录和自动保存验收日志。

## 固定引脚

| 新扩展板 | XIAO信号 | 自检用途 |
| --- | --- | --- |
| CN1 Pad1 | GPIO21 / USER_LED | 扩展板侧NC；只测试XIAO板载LED |
| CN1 Pad19 | GPIO40 | TF电源使能 |
| CN1 Pad21 | GPIO41 | 麦克风DATA |
| CN1 Pad23 | GPIO42 | 麦克风CLK |
| CN1 Pad22 | GPIO9 | TF MOSI |
| CN1 Pad24 | GPIO8 | TF MISO |
| CN1 Pad26 | GPIO7 | TF SCK |
| CN1 Pad28 | GPIO3 / D2 | TF CS |

## 首次上电顺序

1. 只焊第一块，断电检查焊接方向、连锡和异物。
2. 用万用表确认 `3V3_IN-GND`、`VCC_SD_3V3-GND` 没有近似短路。
3. 先给XIAO烧录本自检固件，再与新扩展板插合。
4. 插入已格式化为FAT32的TF卡，打开115200波特率串口。
5. 上电后程序会自动运行一轮；麦克风测试的3秒内说话或轻敲麦克风附近。
6. 自动测试全部通过后，再运行 `C` 做50次TF断电重挂载。
7. 用命令 `0` 和 `1` 配合万用表确认TF电源约为0V和3.3V。

## 编译与烧录

```bash
cd firmware/esp32s3_hardware_self_test
pio run
pio run --target upload
pio device monitor
```

## 串口命令

| 命令 | 功能 |
| --- | --- |
| `A` | 全部测试，包含10次TF断电重挂载 |
| `S` | TF挂载、64KiB写入、读回逐字节校验和测试文件删除 |
| `C` | 50次TF断电重挂载 |
| `M` | 3秒麦克风数据活动测试 |
| `D` | 诊断麦克风CLK/DATA翻转及DATA停钟后的短路状态 |
| `L` | XIAO板载USER LED闪烁3次，需人工观察 |
| `0` | TF断电并将四根SPI信号设为高阻，供测量关断电压 |
| `1` | TF上电但不挂载，供测量开启电压 |
| `P` | 打印连接器映射 |
| `?` | 打印命令菜单 |

自检中的麦克风PASS只代表时钟、数据和采样存在活动，最终音质、底噪和声孔是否受阻仍需使用正式固件录一段音频并试听。

若 `M` 失败，先运行 `D`。`D` 会在XIAO的GPIO42和GPIO41端检查PDM时钟与数据翻转，并在停钟后用内部上拉/下拉检查DATA是否存在硬短路。该诊断不能代替万用表对MIC1焊盘的供电和端到端通断测量。

T3902为底部端子LGA封装，回流后Pin1/Pin4/Pin5被器件本体完全覆盖，不可直接探测。本版PCB的GPIO41、GPIO42过孔有阻焊覆盖，也不能作为万用表测试点。禁止刮阻焊或用表笔尝试接触MIC1底部焊盘；在C3供电正常且`D`确认DATA始终高阻后，只能通过整体回流或更换MIC1继续排查。
