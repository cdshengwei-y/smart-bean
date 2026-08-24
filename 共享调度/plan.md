# 2026-08-24 日清夜跑计划

## 唯一目标

收口录音豆跨平台发布门禁：整理并复跑 Windows/macOS 安装包、Obsidian 图标、插件安装和重复启动保护的本地验收，形成可交付状态与明确阻塞项。

## 背景

- 当天协商结果 `/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/协商结果-2026-08-24.md` 已记录用户选择“3”，并明确本地验收已有 `PASS=25 / FAIL=0 / N/A=4`。
- macOS 同事测试包、Obsidian 插件资源、Windows 安装器单元测试和重复执行保护已有本地证据；Developer ID 签名/公证、Windows 客户 EXE 签名、Defender/SmartScreen 与干净客户流程仍缺证据。

## 范围

- 复跑 `deployment/verify_deployment.py` 及现有 Windows 安装器、Obsidian 插件和重复启动保护测试。
- 核验 macOS/Windows 包结构、图标、插件资源、安装与重复启动保护，并汇总 PASS、FAIL、N/A 证据。
- 对缺少发布机证书、公证凭证或干净系统的项目保留阻塞，不升级为正式发布完成。

## 非目标

- 不宣称正式客户发布完成，不伪造签名、公证、Defender/SmartScreen 或干净 Windows 结果。
- 不连接生产服务、不读取真实用户数据、不操作真实硬件，不部署、不烧录、不修改线上配置。
- 不提交或推送业务项目；本轮 Git 仅用于共享计划材料。

## 执行者1-4分工

1. 执行者1：复跑部署门禁和包结构、图标、插件资源检查，回传命令、结果和证据。
2. 执行者2：复跑 Windows 安装器与重复启动保护测试，记录实际 PASS/FAIL/N/A。
3. 执行者3：核验 macOS 本地安装/启动边界，整理签名、公证和客户系统缺口；无证据则标记阻塞。
4. 执行者4：独立审计证据汇总、状态语义和停止条件，仅回传不直接写共享文件。

## 依赖顺序

1. 先读取协商结果和现有发布记录。
2. 执行者1、2完成本地门禁；执行者3同步核验 macOS 边界。
3. 汇总真实结果与 N/A/阻塞项后，由执行者4独立终审。

## 约束

- 只记录可复现的本地证据，区分本地验证与真实客户链路。
- 执行者在各自任务会话回传，调度者统一落盘共享计划和结果。
- 不因单项构建或测试通过而宣称整轮 PASS；缺少真实证据的项目保持 N/A/阻塞。

## 测试

- `python3 deployment/verify_deployment.py`（若目标项目存在该脚本）。
- 现有 Windows 安装器、Obsidian 插件和重复启动保护定向测试。
- `git diff --check`、`git status --short` 仅用于检查；不在业务项目提交。

## 验收标准

- 输出本地门禁的实际 PASS/FAIL/N/A 计数、命令和证据位置。
- Windows/macOS 包结构、图标、插件安装和重复启动保护均有可回读证据或明确 N/A 原因。
- Developer ID/公证、Windows 签名、Defender/SmartScreen、干净客户流程未取得证据时，交付状态明确为部分收口/阻塞，不冒充正式发布完成。

## 停止条件

- 需要真实客户系统、发布证书、公证、生产服务、真实用户数据或硬件操作。
- 任一本地门禁失败、证据无法回读或发现敏感信息泄露风险。

## 产出路径

- 当前计划：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/plan.md`
- 计划归档：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/plan-2026-08-24.md`
- 当天协商结果：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/协商结果-2026-08-24.md`
- 目标项目：`/Users/jh-05/Documents/smart recording beam`
