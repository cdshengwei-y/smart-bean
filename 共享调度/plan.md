# 2026-08-12 日清夜跑计划

## 唯一目标

优化并验收“飞书远程控制 Codex”本地桥接流程，聚焦手机飞书下发任务到本机 Codex 的最小可测闭环：命令解析、白名单、模型配置、无进度超时、隔离 worktree、模拟报告和风险边界。

## 背景与证据

- 2026-08-12 “日清夜跑16点40”协商任务中，用户明确选择：“今天晚上跑那个飞书远程控制codex吧，把它优化一下。”
- 已读取协商结果文件：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/协商结果-2026-08-12.md`，状态为“用户已选择唯一目标”。
- 目标项目已定位为：`/Users/jh-05/Documents/手机远程操控Codex`。
- 项目目标文件显示 MVP-1 要实现：手机飞书消息进入本机服务，在白名单项目中安全调用 Codex，并把状态、修改、验证结果、风险和后续动作回传飞书。
- README 显示当前能力包括接收、鉴权、排队、取消、隔离执行和报告；明确不支持远程应用变更、提交、推送、部署或删除工作区。
- 本地验收报告显示 2026-08-04 MVP-1 代码验收已达成，但真实飞书和真实 Codex 尚未生产闭环验证。
- 当前项目已有未提交改动，涉及 `CHANGELOG.md`、`config/config.example.json`、`src/adapters/feishu-command.ts`、`src/adapters/feishu.ts`、`src/core/config.ts`、`src/runner/codex-runner.ts`、`src/service/task-executor.ts` 和相关测试等；这些改动疑似已在优化默认项目、模型信息、@机器人消息清理、模型配置透传和无进度超时。
- 历史验收日志显示 `node --test --experimental-strip-types test/*.test.ts` 曾有 37 项通过，本地模拟链路可产生“已受理”和“执行完成，等待你检查”的手机回执样例；`doctor` 曾因缺少 `config/config.local.json` 报错，但当前本机已存在该配置文件。

## 范围

- 审计并验证飞书命令解析：`项目 <别名>：<任务>`、`/projects`、`/status`、`/cancel`、`确认应用`、`放弃`、模型信息问答和默认项目指令。
- 审计并验证飞书适配器：固定 chat、白名单用户、分页、幂等、@机器人前缀清理、发送/回复参数数组和错误信封处理。
- 审计并验证 Codex runner：模型 provider/model/base_url 配置透传、`workspace-write` 沙箱、`approval_policy=never`、stdin prompt、JSONL 解析、全局超时、无进度超时、取消和报告 schema。
- 审计并验证 task executor 与 worktree：每个任务进入独立 Git worktree，不污染原仓库；失败报告包含可读错误原因；危险动作保持 `needs_attention`。
- 复跑本地自动测试、doctor 和 simulate，形成可复现证据。
- 若证据充分且已有改动需要最小修正，只允许修改“飞书远程控制 Codex”项目中与上述链路直接相关的文件，并补对应测试。

## 非目标

- 不连接真实生产飞书群，不向真实飞书发送消息，不读取私聊、日历、真实用户数据或生产服务。
- 不调用真实 Codex 修改业务仓库；模拟测试不得触发真实模型执行。
- 不提交、不推送、不创建 PR、不部署、不安装或启用 LaunchAgent。
- 不启用远程应用变更、提交、推送、部署或删除工作区能力。
- 不处理与远程控制 Codex 无关的录音豆 App、固件、PCB、硬件、Obsidian 同步或日报自动化。

## 执行者分工

### 执行者1：输入与飞书适配器

- 审计 `src/adapters/feishu-command.ts`、`src/adapters/feishu.ts` 和对应测试。
- 覆盖默认项目指令、模型信息问答、@机器人前缀清理、固定 chat 过滤、白名单、分页和幂等。
- 输出命令解析矩阵、飞书适配器边界和定向测试结果。

### 执行者2：配置、模型与 Codex runner

- 审计 `src/core/config.ts`、`config/config.example.json`、`src/runner/codex-runner.ts` 和对应测试。
- 验证 modelProvider/model/baseUrl/progressTimeoutMs 的配置校验、参数数组构造、敏感环境变量剔除、JSONL 和报告 schema。
- 输出 runner 参数清单、超时/取消行为证据和风险点。

### 执行者3：任务执行、状态机与模拟链路

- 审计 `src/service/task-executor.ts`、`src/service/bridge-service.ts`、`src/cli/simulate.ts`、`src/core/state-machine.ts` 和端到端测试。
- 验证任务入队、状态查询、取消、独立 worktree、失败回执、`needs_attention` 停止和模拟报告。
- 输出端到端模拟记录和原仓库未污染证据。

### 执行者4：独立复核与交付记录

- 复核前三项结论、项目未提交改动范围、测试覆盖和安全边界。
- 复跑全量 `node --test`、`doctor`、`simulate`，保留完整输出。
- 生成最终验收记录，明确已验证、未验证、N/A 和下一步人工实测门禁。

## 依赖顺序

1. 执行者1与执行者2可并行，先固定输入解析和 runner 配置合同。
2. 执行者3依赖执行者1的命令合同与执行者2的 runner 合同，验证端到端模拟链路。
3. 若任一执行者发现确定缺陷，只允许做最小本地修正并补定向测试。
4. 执行者4等待前三项回传后终审，复跑全量测试与本地模拟。
5. 调度者最后汇总，不能提前把真实飞书或真实 Codex 闭环写成已完成。

## 约束

- 只读取 `/Users/jh-05/Documents/手机远程操控Codex`、共享调度区、当前协商结果和必要的本地测试输出。
- 保留项目中已有未提交改动，不覆盖、不回滚、不提交。
- 不输出或传播 `config/config.local.json` 中的敏感配置值；如需引用，只描述字段存在和用途。
- 所有测试必须使用本地模拟或假进程，不向真实飞书发送消息，不调用真实 Codex 模型。
- 任何通过数、失败原因和行为结论必须来自实际命令输出；不确定项写 N/A 或待人工确认。

## 测试

- 项目全量测试：在 `/Users/jh-05/Documents/手机远程操控Codex` 执行 `node --test --experimental-strip-types test/*.test.ts`。
- Doctor：执行 `node --experimental-strip-types scripts/doctor.ts`，检查 Node、Git、Codex、飞书 CLI、配置和运行目录。
- 本地模拟：执行 `node --experimental-strip-types src/cli/simulate.ts '项目 bridge：只读检查当前项目状态并报告风险'` 或等价只读任务。
- Git 污染检查：执行 `git status --short` 和必要的 `git diff --stat`，确认夜跑只改目标范围内文件且不提交。
- 若修改 runner 或飞书适配器，追加或复跑对应定向测试：`test/runner-codex.test.ts`、`test/feishu-command.test.ts`、`test/feishu-adapter.test.ts`、`test/service-e2e.test.ts`。

## 验收标准

- 形成一份远程控制 Codex 链路验收记录，覆盖输入、鉴权、任务、runner、worktree、报告和安全边界。
- 全量 Node 测试实际通过；若失败，记录首个失败、复现命令和归因，最终状态为 PARTIAL/FAIL。
- Doctor 至少能确认 Node、Git、Codex、飞书 CLI 和本机配置存在；若外部登录或策略导致失败，必须明确 N/A 或待人工处理。
- 本地 simulate 能完整产生受理和完成/需关注报告，且不连接真实飞书、不调用真实 Codex。
- 不发生真实飞书发送、真实业务仓库修改、Git 提交/推送、部署、LaunchAgent 安装或生产服务连接。

## 停止条件

- 需要真实飞书群发消息、真实 Codex 远程执行、生产凭据、手机实机、外部部署或用户二次确认才能继续时立即停止该分支并标记 N/A。
- 发现未提交改动来源不明且会被夜跑覆盖时停止修改，只做只读审计并报告冲突。
- Git 可访问 PLAN.MD 链接无法生成或自查失败时，停止飞书写入并报告原因，不用本地路径或飞书文件链接冒充。
- 全量测试出现环境性失败时，保留完整首个错误和复现命令；不得通过连接外部服务绕过。

## 产出路径

- 当前计划：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/plan.md`
- 计划归档：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/plan-2026-08-12.md`
- 执行产出目录：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/2026-08-12-飞书远程控制Codex优化/`
- 内容来源任务：Codex 任务“日清夜跑16点40”，threadId `019ff3d5-b10c-7ea1-a063-12aa825d08c5`
- 执行任务：Codex 任务“日清夜跑17点”，threadId `019ff045-8edb-7a40-a3fc-5aa83d0d039a`
