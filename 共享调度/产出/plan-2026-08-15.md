# 2026-08-15 日清夜跑计划

## 唯一目标

修复远程 Codex 的任务终态语义：把“只读检查、无文件变更、结果已回传”的任务与真正需要用户确认的任务区分开，补齐状态机、报告和回归测试，让手机/飞书用户看到准确的完成状态。

## 背景与证据

- 用户在 2026-08-15 的 16:40 协商截图中明确选择候选目标 1；本计划以该目标为准，不改写产品方向。
- 2026-08-15 真实受控冒烟已证明“飞书 → 本地桥接 → Codex → 回传”五节点可闭环，但只读任务报告为 `awaiting_confirmation`，同时 `changedFiles: []`、无文件变更；现有记录把它解释为“结果需要用户进一步处理”，用户体验上无法直接区分“已完成只读检查”和“等待确认的可变更任务”。
- 当前目标项目 `/Users/jh-05/Documents/手机远程操控Codex` 已有未提交改动，`src/service/bridge-service.ts` 当前按 `report.status === "needs_attention"` 统一映射为 `awaiting_confirmation`；相关状态机、报告格式化和 `test/service-e2e.test.ts` 已有可复用测试入口。
- 上轮本地门禁曾达到全量 Node 测试 `53/53`、doctor、simulate 和 `git diff --check` 通过；本轮只围绕终态语义做最小修改与回归，不把既有门禁结果写成新的完成证据。

## 范围

- 梳理结构化报告 `succeeded` / `needs_attention`、任务状态 `succeeded` / `awaiting_confirmation` / `failed` 与工作区 `changedFiles` 的现有合同，定义可审计的终态分类规则。
- 对明确只读、无文件变更且结果已完整回传的任务，输出“已完成/无需确认”的终态和移动端/飞书可读报告；不得仅凭摘要文字或空数组偶然推断。
- 对确实包含待用户确认、危险动作或后续应用步骤的任务，继续保留 `awaiting_confirmation`，并保留确认/放弃入口与安全门禁。
- 补状态机、桥接服务、移动端报告和端到端回归测试；覆盖只读成功、真实需要确认、失败、取消和通知重试等相邻边界。
- 复跑定向测试、全量 Node 测试、doctor、simulate、`git diff --check`，记录实际结果和首个失败。

## 非目标

- 不扩展远程执行能力，不启用远程应用、删除、部署、提交、推送或生产写操作。
- 不修改目标项目之外的业务代码，不回滚或覆盖已有未提交改动，不提交/推送目标业务项目 Git。
- 不读取私聊、日历、真实用户数据、生产服务或 `config/config.local.json` 中的敏感值。
- 不把一次真实冒烟、单项测试通过或构建成功写成整轮 PASS；真实飞书链路本轮只作为已存在的背景证据，不重复发送任务。
- 不涉及真实板卡、固件烧录、供电/功耗、麦克风、TF、BLE、Reset 或实机 App 联调，因此不新增人工实板验收任务。

## 执行者分工

### 执行者1：终态语义与状态机合同

- 审计 `src/core/types.ts`、`src/core/state-machine.ts`、`src/core/task-store.ts`、`src/service/bridge-service.ts` 的终态转换和字段语义。
- 设计并实现最小、可测试的终态分类规则：只读无变更完成、需要确认、失败、取消互不混淆；保留现有危险操作确认门禁。
- 回传状态、已读输入、实际证据、修改文件、测试命令与结果、N/A/阻塞项、下一步和绝对截止时间；不直接写共享调度文件。

### 执行者2：报告与用户可见状态

- 审计并修改 `src/reporting/mobile-report.ts`、`src/reporting/structured-report.ts`、状态查询/通知相关路径。
- 确保“只读检查完成且无文件变更”在飞书/手机报告中明确显示完成，不再要求无必要的确认；真正需要确认的任务保留清晰的下一步和安全提示。
- 回传实际报告样例、脱敏前后差异、测试命令与结果、N/A/阻塞项、下一步和绝对截止时间；不直接写共享调度文件。

### 执行者3：回归测试与边界覆盖

- 审计并补充 `test/service-e2e.test.ts`、`test/core-store.test.ts`、报告相关测试和必要的命令/状态测试。
- 至少覆盖：只读 `changedFiles=[]` → `succeeded`；`needs_attention` 且确有确认语义 → `awaiting_confirmation`；存在变更但报告异常；失败、取消和通知重试不回归。
- 回传测试命令与逐项结果、实际证据、N/A/阻塞项、下一步和绝对截止时间；不直接写共享调度文件。

### 执行者4：独立质量门禁

- 等待前三项回传后，独立复跑定向/全量测试、doctor、simulate、`git diff --check` 和状态报告审计。
- 检查没有通过“无变更”掩盖真正的确认需求，也没有把失败/取消误报为完成；确认目标项目既有未提交改动被保留。
- 若门禁通过，仅做本地/模拟验证，不重复真实飞书任务；回传完整终审证据、最终 PASS/PARTIAL/FAIL、N/A/阻塞项和绝对截止时间。共享调度文件由调度者统一落盘。

## 依赖顺序

1. 执行者1先固定终态分类合同；执行者2、3可在合同明确后并行落地报告和测试。
2. 调度者收敛接口冲突，只保留与目标直接相关的最小修改。
3. 执行者4在前三项回传齐全后独立复跑全部本地门禁。
4. 本轮不重复真实飞书冒烟；若本地语义无法证明五种边界，停止并按实际证据标记 PARTIAL。

## 约束

- 只读取已授权的目标项目、共享调度区、当日用户选择截图、上一轮回传/修改记录和必要测试输出。
- 保留目标项目现有未提交改动；执行者不直接写共享计划、任务表、回传目录或产出目录。
- 任何外部服务、真实飞书、真实 Codex、网络或权限异常均如实记录，不改用机器人身份，不绕过确认门禁。
- 报告不得输出 token、secret、私钥、Cookie、真实配置值或本地敏感路径；只保留脱敏错误和可复核摘要。

## 测试与核验

- 定向状态/服务测试：`node --test --experimental-strip-types test/core-store.test.ts test/service-e2e.test.ts`。
- 定向报告/命令测试：按实际修改路径补跑对应 `test/*.test.ts`，记录命令和结果。
- 全量测试：`node --test --experimental-strip-types test/*.test.ts`。
- Doctor：`node --experimental-strip-types scripts/doctor.ts`。
- 本地模拟：`node --experimental-strip-types src/cli/simulate.ts '项目 demo：只读检查当前项目状态并报告风险'`；只证明本地模拟，不冒充真实服务。
- Git 检查：`git diff --check`、`git status --short` 和必要的 `git diff --stat`；不提交、不推送目标项目。

## 验收标准

- 只读检查、无文件变更、结构化结果已完整回传的任务最终状态明确为“已完成/无需确认”，报告显示 `changedFiles: []` 且不出现误导性的等待确认提示。
- 真正需要用户确认的危险动作、应用步骤或待处理结果仍为 `awaiting_confirmation`，确认/放弃安全门禁不被绕过。
- 失败、取消、通知失败重试和状态查询边界均有回归测试；定向与全量门禁实际结果如实记录。
- 目标项目已有未提交改动被保留；整轮最终状态只能依据证据判定 PASS、PARTIAL 或 FAIL。

## 停止条件

- 无法从结构化报告、任务输入和工作区摘要可靠区分“只读完成”和“需要确认”时，停止改写终态，保留现状并标记阻塞。
- 任一本地门禁失败，停止后续扩展，只保留首个失败和复现命令。
- 需求扩大到真实用户数据、生产服务、远程写操作、提交/推送、部署或硬件操作时立即停止并等待另行授权。

## 产出路径

- 当前计划：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/plan.md`
- 计划归档：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/plan-2026-08-15.md`
- 执行产出目录：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/2026-08-15-远程Codex任务终态语义/`
- 主要证据：`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/真实冒烟-2026-08-15.md`、`/Users/jh-05/Documents/ChatGPT/日清夜跑执行者/共享调度/产出/修改记录-2026-08-14.md`、`/Users/jh-05/Documents/手机远程操控Codex/src/service/bridge-service.ts`、`/Users/jh-05/Documents/手机远程操控Codex/test/service-e2e.test.ts`
