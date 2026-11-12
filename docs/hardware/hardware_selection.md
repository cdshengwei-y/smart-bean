# AI录音豆历史原型选型与当前硬件基线

整理日期：2026-07-09  
适用阶段：原型验证 / MVP / 第一版工程样机  
价格口径：以下价格为 2026-07-09 查询到的公开页面参考价，不含税费、运费、平台优惠和汇率波动。

> 本文第1至8节保留早期开发板原型和采购记录，不再作为已投产新扩展板的设计依据。当前新板已经删除物理录音按键，通过BLE控制录音，并加入GPIO40控制的TPS22919 TF电源。当前连接以第5节末尾的关键引脚、`schematic_full_flow_design.md`和到板验收文档为准。

## 1. 选型目标

V0.1 的目标不是直接做量产硬件，而是尽快验证完整闭环：

- 早期原型使用按键开始/停止录音；当前正式方案改为手机App通过BLE控制
- 录音保存为 WAV 文件
- 音频持续写入一个 WAV 文件，停止时补写文件头并保存到 microSD
- 通过 USB/读卡器导出
- 后端完成转写和 AI 结构化分析

因此硬件选型原则是：

- 优先选集成度高、资料多、能快速跑通的开发板
- 第一版尽量少焊接、少自研电路
- 设备端只做录音、存储、状态提示，不在设备端跑 AI
- 所有 API Key 和智能分析都放在后端

## 2. 两版主控方案

考虑到 XIAO BLE Sense 也集成了 PDM 麦克风，可以把主控设计拆成两版：

| 版本 | 主控 | 单价参考 | 无线能力 | 存储路线 | 适合场景 | 取舍 |
| --- | --- | ---: | --- | --- | --- | --- |
| A 版：标准录音版 | Seeed Studio XIAO ESP32S3 Sense | USD 13.90 | Wi-Fi / BLE | 板载 microSD 卡槽，直接保存 WAV 分片 | 录音豆 MVP、长录音、离线缓存、后续 Wi-Fi 上传 | 体积和功耗略高，但最适合先把“录音 + SD 保存 + 后端分析”跑通。 |
| B 版：BLE 轻量版 | Seeed Studio XIAO nRF52840 Sense / XIAO BLE Sense | USD 15.90 | BLE 5.x | 默认无板载 microSD；可走 BLE 传输、USB 导出，或外接 SPI 存储/TF 模块 | 低功耗佩戴、短录音片段、事件触发录音、TinyML 声音/动作检测 | 内存和存储更紧张，不适合作为第一版长时间 WAV 录音主路线。 |

### A 版：XIAO ESP32S3 Sense 标准录音版

推荐作为第一版主线。它已经集成数字麦克风、Wi-Fi/BLE、microSD 卡槽、8MB PSRAM 和 8MB Flash，能直接完成录音、SD 卡缓存，再通过 USB/SD 卡或后续 Wi-Fi 上传到后端。

适合目标：

- 连续录音 1 小时以上
- 手动停止前持续录音并保存为一个 WAV 文件
- SD 卡离线缓存
- 后端转写和 AI 总结
- 后续升级 Wi-Fi 上传

### B 版：XIAO BLE Sense 轻量版

适合作为低功耗探索版。它基于 nRF52840，集成 PDM 麦克风和 6 轴 IMU，功耗低，适合 BLE 设备、佩戴式传感、事件触发录音和 TinyML 检测。

但它不适合直接替代 A 版做长时间 WAV 录音，主要原因是：

- 没有 Wi-Fi，音频上传主要依赖 BLE 或 USB。
- 默认没有板载 microSD 卡槽，长录音需要外接 SPI Flash、TF 卡模块，或缩短录音片段。
- RAM/Flash 资源明显小于 ESP32S3 Sense，音频缓存、文件系统和 BLE 同步需要更克制地设计。
- 更适合“检测到声音/动作后录一小段”，而不是一直录完整会议。

B 版建议定位：

- 低功耗佩戴版
- BLE 同步版
- 声音事件检测版
- 短片段录音版
- 后续做极小外观或电池续航优化时再推进

## 3. 核心硬件清单

| 类别 | 推荐型号 / 规格 | 数量 | 单价参考 | 小计参考 | 为什么选 |
| --- | --- | ---: | ---: | ---: | --- |
| 主控板 | Seeed Studio XIAO ESP32S3 Sense | 2 | USD 13.90 | USD 27.80 | 集成 ESP32-S3、Wi-Fi/BLE、数字麦克风、microSD 卡槽、8MB PSRAM/Flash 和电池支持，能直接验证录音采集与 SD 存储。买 2 块是为了防止烧录、焊接或调试损坏。 |
| 存储卡 | 8GB microSDHC Class 10 / U1，FAT32 | 1 | USD 4-8 | USD 4-8 | 已采购。官方文档建议 Sense 版本 microSD 使用 FAT32；8GB 对第一版 16kHz mono WAV 验证已足够。 |
| 电池 | 3.7V LiPo 500mAh，带保护板和 JST-PH 插头 | 2 | USD 7.95 | USD 15.90 | 体积小，适合钥匙扣、胸针或小盒子形态；容量足够验证数小时录音。带保护板更适合原型阶段。 |
| 录音按键（历史原型） | 6mm 轻触开关，20 个装 | 1 | USD 2.50 | USD 2.50 | 仅保留为早期采购记录；当前新扩展板不装录音按键。 |
| 电源开关 | SPDT 滑动开关 | 2 | USD 0.95 | USD 1.90 | 用于物理切断电池供电，方便长期存放、运输和调试；比纯软件休眠更直观。 |
| 电池线 | JST-PH 2pin 线，100mm | 2 | USD 0.75 | USD 1.50 | 用于电池、电源开关、主控板之间转接，减少直接剪电池线的风险。 |
| 原型固定 | Adafruit Perma-Proto Half-sized Breadboard PCB | 1 | USD 4.50 | USD 4.50 | 固定按钮、开关和线材，比散线更可靠；焊接结构清楚，方便排查问题。 |
| USB-C 数据线 | 支持数据传输的 USB-C 线 | 1 | USD 5-10 | USD 5-10 | 用于烧录程序、串口调试和临时供电。注意不要买只能充电的线。 |
| 耗材 | 杜邦线、热缩管、双面胶、热熔胶 | 1 批 | USD 5-10 | USD 5-10 | 固定电池、线材和按钮，降低拉扯断线风险。 |

核心材料预算参考：约 USD 67.10 - 80.10。

## 4. 可选但建议准备

| 类别 | 推荐型号 / 规格 | 数量 | 单价参考 | 为什么选 |
| --- | --- | ---: | ---: | --- |
| 独立充电板 | Adafruit Micro-Lipo Charger USB-C | 1 | USD 5.95 | 用来单独测试和充电 3.7V LiPo，能排除主控板充电、电池本体、线序等问题。 |
| XIAO 扩展底板 | Seeed Grove Base for XIAO | 1 | USD 3.90 | 如果不想一开始焊太多线，可以辅助原型连接；但最终小型化时大概率会去掉。 |
| 万用表 | 入门数字万用表 | 1 | USD 10-25 | 强烈建议准备，用于确认电池正负极、开关通断、短路和供电电压。 |
| 电烙铁 + 焊锡 | 细头温控烙铁 | 1 | USD 15-40 | 焊接按钮、电源开关、电池线和原型板。 |
| 3D 打印外壳 | 定制小外壳 | 1 | 视服务而定 | 等录音和 SD 保存稳定后再做，第一版重点是开孔位置、按钮手感和麦克风无遮挡。 |

## 5. 主控板为什么选 XIAO ESP32S3 Sense

选择它的主要原因是集成度高：

- ESP32-S3 双核 MCU，性能足够做 PDM 麦克风采集、WAV 封装和 SD 写入
- 板载数字麦克风，不需要第一版额外选麦克风模块
- 板载 microSD 卡能力，适合本地缓存音频
- 支持 Wi-Fi/BLE，后续 V0.2 可以升级无线上传
- 支持锂电池供电和充电管理，适合可穿戴/便携设备
- 体积小，适合“录音豆”形态
- Seeed 官方资料比较完整，降低调试风险

依据 Seeed 官方 `XIAO ESP32S3 Exp. Board v1.0` 原理图和当前新扩展板的实体接触位置，关键引脚为：

- 数字麦克风 CLK：GPIO42
- 数字麦克风 DATA：GPIO41
- SD 卡 CS：GPIO3（D2；新扩展板 CN1 Pad28，对应 XIAO J3 Pad2）
- SD 卡 SCK：GPIO7
- SD 卡 MISO：GPIO8
- SD 卡 MOSI：GPIO9
- XIAO 板载 USER LED：GPIO21（新扩展板对应 CN1 Pad1，扩展板侧为 NC）

注意：连接器脚号、封装 Pad 号和 MCU GPIO 号是三套不同编号，必须按实体接触位置核对。新扩展板的 `CN1 Pad1 = NC` 不会影响 XIAO 主板内部的 USER LED，只是不再把 GPIO21 引入扩展板电路。

这和项目第一版“录音 + SD 卡保存”的需求高度匹配。

## 6. 存储和录音参数

第一版建议保持简单：

| 参数 | 建议值 | 原因 |
| --- | --- | --- |
| 音频格式 | WAV / PCM | 调试方便，电脑直接可播放，后端处理简单。 |
| 采样率 | 16kHz | 语音转写足够，文件体积可控。 |
| 位深 | 16bit | 常见语音录音配置，兼容性好。 |
| 声道 | Mono | 板载单麦克风，单声道即可。 |
| 单文件时长 | 手动停止 | 默认不自动切片；后续上传阶段可按时间拆分或分段处理。 |
| 文件系统 | FAT32 | 与官方 microSD 使用建议一致。 |

文件大小估算：

```text
16,000 samples/s * 16 bit * 1 channel = 256,000 bit/s
256,000 bit/s / 8 = 32 KB/s
约 1.9 MB/min
约 9.6 MB / 5 分钟（容量参考）
约 115 MB / 小时
```

已采购的 8GB microSD 理论上可保存约 60-70 小时的 16kHz mono WAV。实际可用容量、文件系统开销和异常文件会降低可用时长，但对 MVP 验证已足够。

## 7. 不建议第一版购买或投入的东西

| 项目 | 为什么暂时不建议 |
| --- | --- |
| 定制 PCB | 录音链路、功耗、按键逻辑还没验证，太早做 PCB 容易返工。 |
| 手机 App | 开发成本高，会分散重点。先用 USB/SD 导出 + 网页后台更快。 |
| 高级麦克风阵列 | 第一版先验证单麦克风转写效果，再决定是否升级。 |
| 实时转写模块 | 会引入网络、功耗、延迟和断线问题。MVP 先做离线上传处理。 |
| 大容量电池 | 会影响体积和佩戴体验。先用 500mAh 验证续航，再按实测调整。 |
| 超小外壳 | 过早追求外观会压缩调试空间。先做可开合、可改线的工程样机。 |

## 8. 第一单建议

建议第一单按“能开工、能容错”购买：

| 序号 | 材料 | 数量 | 备注 |
| ---: | --- | ---: | --- |
| 1 | XIAO ESP32S3 Sense | 2 | 一块开发，一块备用。 |
| 2 | 8GB microSDHC Class 10 / U1 | 1 | 已采购；格式化为 FAT32。 |
| 3 | 3.7V 500mAh LiPo | 2 | 优先买带保护板、可靠品牌。 |
| 4 | USB-C 数据线 | 1 | 确认可传数据。 |
| 5 | 6mm 轻触按键 | 1 包 | 开始/停止录音。 |
| 6 | SPDT 滑动开关 | 2 | 总电源开关。 |
| 7 | JST-PH 2pin 线 | 2 | 方便电池接线和转接。 |
| 8 | Perma-Proto 或小洞洞板 | 1 | 固定按键和开关。 |
| 9 | 杜邦线、热缩管、双面胶 | 1 批 | 原型固定和走线。 |
| 10 | 万用表、电烙铁、焊锡 | 各 1 | 如果已有可不买。 |

## 9. 风险和注意事项

- LiPo 电池有极性，接反可能损坏主控板或电池；焊接前必须用万用表确认 BAT+ 和 GND。
- 录音设备必须有明确录音提示灯，不建议做无提示录音。
- SD 卡写入时要避免突然断电导致 WAV 文件头未写完；代码里需要在停止录音时正确补写 WAV header。
- 当前正式固件不使用外接录音按键；历史原型若继续使用按键仍需软件去抖。
- 板载麦克风适合 MVP 验证，但最终音质可能受外壳开孔、佩戴位置、环境噪声影响。
- Adafruit 页面显示 6mm 轻触按键当前缺货，可以用同规格 6mm 轻触开关替代。

## 10. 资料来源

- Seeed Studio XIAO ESP32S3 Sense 商品页：https://www.seeedstudio.com/XIAO-ESP32S3-Sense-p-5639.html
- Seeed Studio XIAO nRF52840 Sense 商品页：https://www.seeedstudio.com/Seeed-XIAO-BLE-Sense-nRF52840-p-5253.html
- Seeed XIAO ESP32S3 官方入门文档：https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/
- Seeed XIAO nRF52840 Series 官方文档：https://wiki.seeedstudio.com/XIAO_BLE/
- Seeed XIAO ESP32S3 Sense 麦克风文档：https://wiki.seeedstudio.com/xiao_esp32s3_sense_mic/
- Seeed XIAO ESP32S3 Sense microSD 文档：https://wiki.seeedstudio.com/xiao_esp32s3_sense_filesystem/
- Adafruit 3.7V 500mAh LiPo：https://www.adafruit.com/product/1578
- Adafruit 6mm 轻触按键：https://www.adafruit.com/product/367
- Adafruit SPDT 滑动开关：https://www.adafruit.com/product/805
- Adafruit JST-PH 2pin 线：https://www.adafruit.com/product/261
- Adafruit Perma-Proto Half-sized PCB：https://www.adafruit.com/product/1609
- Adafruit Micro-Lipo Charger USB-C：https://www.adafruit.com/product/4410
