# 转写后端接口

此目录定义语音模型与存储服务的稳定接入接口。当前内置智谱 GLM-ASR，并支持用户配置的 HTTP ASR；包括 Qwen3-ASR 在内的第三方服务均通过手机 App 的自定义 ASR 配置接入。模型选择由手机 App 写入本机配置。ASR 与 AI 文字处理是两个独立队列，文本模型不可用时仍会先保存完整原文，恢复后可直接重试，不会重新下载或转写。

存储、转写和 Obsidian 同步服务的开放合同见
[`docs/open_service_contracts.md`](docs/open_service_contracts.md)。默认 MinIO/S3
配置与既有调用方式保持兼容。

手机 App 的“ASR服务 → 自定义服务”可保存多个标准 HTTP ASR 的地址、路径、
字段名和可选 API Key；保存后自动切换到当前服务。密钥只保存在后端；服务端必须运行
`status_server.py`，且不可信网络部署时必须使用 HTTPS。

## 工作内容筛选

处理链路为：

```text
WAV -> ASR 完整转写 -> 原文 Markdown -> MinIO notes/ -> Obsidian
                                  |
                                  v
                         AI 优化 -> 内容筛选 -> 最终 Markdown
```

筛选器把任务、项目进展、需求、决策、会议结论、计划、风险、待办、业务信息、技术讨论和工作协调视为工作内容，删除纯私人生活和闲聊。模型被要求保留原意且不能补充原文没有的事实。

当前实现连接 OpenAI 兼容的局域网文本模型，需要配置：

```text
WORK_FILTER_BASE_URL=http://局域网模型地址:端口
WORK_FILTER_CHAT_PATH=/v1/chat/completions
WORK_FILTER_MODEL=实际模型ID
WORK_FILTER_API_KEY=
```

没有识别出工作内容时，最终 Markdown 仍会保留元数据但不包含私人或无关文字。完整 ASR 文字保存在 SQLite 的 `transcript` 字段，优化稿保存在 `optimized_text`，筛选结果保存在 `filtered_text`；状态接口不会向手机返回这些全文。

为了避免私人内容因配置错误被原样上传，正式扫描器只负责 ASR 和原文笔记；AI 工作器在模型未配置或调用失败时将任务标记为 `ai_failed`，原文笔记保持可用，不会把完整转写当成最终筛选结果。

## 本地模型需要实现的接口

默认模型选择器是：

```text
app/transcription/model_registry.py
```

默认选中智谱 GLM-ASR，实际服务配置为：

```text
ASR_BASE_URL=http://192.168.0.153:10086
ASR_HEALTH_PATH=/health
ASR_TRANSCRIBE_PATH=/transcribe
ASR_TIMEOUT_SECONDS=600
```

已使用临时中文 WAV 对智谱 GLM-ASR 真实服务完成验证。服务健康检查和转写均成功，详细合同见 `docs/asr_api_contract.md`。

## 切换转写模型

手机连接 `status_server.py` 后，控制页的“语音转文字”区域会显示可用模型。切换只影响尚未开始处理的任务，正在转写的录音会继续由原模型完成。

- **智谱 GLM-ASR**：当前默认模型，继续使用 `ASR_BASE_URL` 的局域网服务。
- **第三方 ASR（包括 Qwen3-ASR）**：在手机的“自定义 ASR服务”中配置服务地址、转写路径、字段映射和认证信息。保存后会自动选中该服务；标准 HTTP 接口合同见 `docs/open_service_contracts.md`。

转写请求使用 `multipart/form-data` 的 `file` 字段，接口返回 JSON 的 `text` 字段。适配器输入为 `TranscriptionRequest`：

- `audio_path`：后端已经下载到本地的 WAV 路径
- `source_object_name`：MinIO 中的对象路径
- `language`：默认 `zh`
- `prompt`：可选的识别提示
- `metadata`：录音附加信息

模型适配器必须返回 `TranscriptionResult`：

```python
return TranscriptionResult(
    text="完整转写文字",
    language="zh",
    duration_seconds=123.4,
    segments=(),
    model_name="你的本地模型名称",
)
```

模型原始输出无论是字符串、字典还是分段列表，都应在适配器内转换成这个统一结果。后续 MinIO 扫描、任务队列、HTTP API 和 Obsidian 同步只依赖该接口。

## 当前已经实现

- 本地模型输入、输出合同
- HTTP ASR 健康检查和真实转写适配器
- 地址、路径和超时环境变量配置
- HTTP 状态、非 JSON 和空文字错误处理
- 可替换的模型工厂加载器
- WAV 输入校验
- 空转写结果检查
- Obsidian Markdown 生成
- 假模型自动测试
- MinIO `recordings/` 自动扫描
- SQLite 任务状态和 `对象路径 + ETag` 去重
- 新录音使用 `recordings/<device_id>/<filename>.wav`；旧的平铺录音自动归为 `legacy`
- 录音临时下载、ASR 调用和 Markdown 上传到 `notes/`
- 失败状态、最多重试次数和重启恢复
- ASR、原文笔记、AI 优化、内容筛选和最终同步的独立状态与耗时
- 每次 ASR 实际使用的模型 ID、模型名称、结果、耗时和错误独立保存为运行记录
- 管理员可查看历史模型运行记录，并按记录中的模型 ID 排队回放
- 模型回放只生成新的运行记录，不覆盖当前转写、AI 结果、MinIO 笔记或 Obsidian 同步内容
- 独立 `ai_worker.py`，AI 失败后可从已保存的原文或优化稿继续
- `POST /api/jobs/<job_id>/retry-ai` 手动重试 AI，不重复 ASR

## 启动自动扫描

只扫描并登记当前 MinIO 文件，不调用 ASR：

```bash
python3 backend/scanner.py --scan-only
```

扫描并处理一次：

```bash
python3 backend/scanner.py --once
```

常驻运行，每 15 秒检查一次：

```bash
python3 backend/scanner.py
```

macOS 后台启动项模板位于：

```text
backend/launchd/com.ai-recording-bean.scanner.plist
```

安装后运行副本放在 `~/Library/Application Support/AI Recording Bean Backend/`，避免 macOS 禁止 LaunchAgent 执行 Documents 目录中的脚本。`launchd` 在用户登录时自动启动，异常退出后自动重启；运行日志和 SQLite 也保存在该目录的 `data/` 中。

SQLite 默认保存到 `backend/data/transcriptions.db`。同一 `object_name + ETag` 只处理一次，生成的笔记保存到 MinIO 的 `notes/`。
尚未处理但已经从 MinIO 删除的录音会标记为 `missing`，不会反复重试。

扫描器只负责云端录音扫描、转写和写入原文 MinIO `notes/`，可独立部署到云端，不调用文本模型。默认不会写入本机 Obsidian；只有开发或运维人员显式配置 `OBSIDIAN_VAULT_PATH` 时，才会启用旧的本机直写同步。

AI 工作器从同一个 SQLite 队列读取 `ai_pending` 任务，独立调用文本模型并覆盖最终 MinIO 笔记：

```bash
python3 backend/ai_worker.py --once
python3 backend/ai_worker.py
```

macOS 后台模板为 `backend/launchd/com.ai-recording-bean.ai-worker.plist`。DeepSeek 文本优化和筛选默认使用两路分块并发；遇到 429、5xx、超时或截断响应时会按配置自动重试。

本机独立同步任务负责将 `notes/` 写入当前 Obsidian Vault，仅用于开发或运维：

```bash
python3 backend/obsidian_sync.py
```

同步任务会保留目录结构，`notes/bean-a1b2c3/REC_001.md` 会写入
`<Vault>/AI录音豆/bean-a1b2c3/REC_001.md`。可通过环境变量覆盖：

```text
OBSIDIAN_VAULT_PATH=/完整的/Vault/路径
OBSIDIAN_NOTES_FOLDER=AI录音豆
OBSIDIAN_SYNC_DB_PATH=/本机/同步状态.db
```

云端同一 ETag 只写一次；云端 Markdown 更新后会覆盖仍存在的对应笔记，不会删除或修改 Vault 中的其他文件。用户在 Obsidian 本地删除已同步笔记后，同步任务不会再从 MinIO 自动补回该文件。滚动录音的对象名使用同一会话编号和连续分段号，例如 `REC_..._P000.wav`；扫描器会按分段号合并为一篇笔记。

`launchd/com.ai-recording-bean.scanner.plist` 用于云端扫描器，
`launchd/com.ai-recording-bean.obsidian-sync.plist` 仅用于开发机的本地直写验收；
两者不必运行在同一台机器。正式客户交付不安装后者，客户使用“录音豆同步助手”通过一次性激活码同步。

## 给手机查看转写进度与给 Obsidian 同步助手提供最终笔记

手机端需要从扫描器所在的电脑读取转写状态时，在该电脑上运行：

```bash
TRANSCRIPTION_DB_PATH="$HOME/Library/Application Support/AI Recording Bean Backend/data/transcriptions.db" \
PYTHONPATH=backend python3 backend/status_server.py
```

它默认监听 `8787` 端口，提供 `GET /health`、`GET /api/status`、模型选择接口
`POST /api/models/select` 和 AI 重试接口 `POST /api/jobs/<job_id>/retry-ai`。状态接口返回上传、转写、原文笔记、AI 优化、筛选和 Obsidian 同步的阶段、耗时、设备汇总与模型可用性，
不会返回 MinIO 密钥、转写全文或 Obsidian 文件路径。手机 App 中填写该电脑的
局域网地址，例如 `192.168.0.107:8787`。

同一服务还供 macOS“录音豆同步助手”使用，但客户不接触 MinIO：

- `POST /api/sync/activate`：客户输入一次性激活码，后端返回绑定设备的同步令牌。
- `GET /api/sync/notes`：Bearer 令牌只能列出该设备 `status=completed` 的最终 Markdown。
- `GET /api/sync/notes/<id>`：Bearer 令牌只能下载自己列表里的单篇最终 Markdown。

手机 App 的“Obsidian 同步”按账户自动关联默认的一台录音豆，用户无需选择设备。新电脑插件导入
手机配对信息并激活成功后，旧电脑的同步令牌会自动失效；生成但未使用的激活码不会影响原同步电脑。

激活码和同步令牌仅保存 SHA-256 哈希，原文不会写进 SQLite。运维命令会优先使用
`TRANSCRIPTION_DB_PATH`；签发或撤销方式：

```bash
PYTHONPATH=backend python3 backend/manage_sync_access.py issue \
  --device-id bean_ye_2 \
  --expires-at 2026-09-01T00:00:00+08:00

PYTHONPATH=backend python3 backend/manage_sync_access.py revoke \
  --device-id bean_ye_2
```

激活码只能使用一次；撤销按设备使此前签发的全部同步令牌立即失效。生产环境必须让此服务
经由 HTTPS 对外提供，不能将开发用 HTTP 服务或 MinIO 服务直接暴露给客户。

### 移动端账户

Flutter 控制端必须先使用邮箱和密码注册或登录，成功后才进入设备控制页。状态服务提供：

- `POST /api/auth/register`：创建邮箱账户并返回会话令牌。
- `POST /api/auth/login`：验证邮箱和密码并返回会话令牌。
- `GET /api/auth/me`：使用 `Authorization: Bearer <token>` 恢复登录账户。
- `POST /api/auth/logout`：撤销当前会话令牌。

密码采用 PBKDF2-SHA256 哈希存储，移动端会话有效期为 30 天。当前版本不发送邮箱验证邮件。

### 管理员端

管理员端与状态服务运行在同一端口，访问地址为：

```text
http://<服务地址>:8787/admin
```

启动状态服务前必须配置管理员密码；未配置时，管理端及其 API 不可用。可用环境变量
`SYNC_ADMIN_PASSWORD`，或将密码写入权限仅限管理员读取的文件，并配置
`SYNC_ADMIN_PASSWORD_FILE`（后者优先）：

```bash
export SYNC_ADMIN_PASSWORD='请设置一段随机长密码'
TRANSCRIPTION_DB_PATH="$HOME/Library/Application Support/AI Recording Bean Backend/data/transcriptions.db" \
PYTHONPATH=backend python3 backend/status_server.py
```

管理端使用 8 小时的 HttpOnly 本机会话，可完成以下操作：

- 查看账户数、设备归属、当前同步端、近期转写任务、异常任务和 ASR 服务健康状态。
- 按设备或邮箱查看客户绑定关系、同步端数量、处理完成度和最近活动。
- 签发永久或限时的一次性激活码；新码仅在签发成功时显示一次。
- 签发时可选择“激活后替换旧同步端”，用于客户换电脑或强制重新绑定。
- 查看激活码的有效、已使用、已撤销和已过期状态。
- 撤销尚未使用的激活码。
- 查看、撤销单个设备同步令牌，或撤销某设备全部同步令牌。
- 正式设备保存在独立目录中，初始包含`bean_ye_1`和`bean_ye_2`。新增硬件时，先在“授权管理”页登记新的设备 ID，再从激活码下拉框选择它签发，不会把历史录音或测试身份自动识别为新设备。
- 在“模型回放”页查看每次转写实际使用的模型、状态、耗时、错误和结果预览；对已完成记录可按该模型排队重新转写。

管理员接口不会返回任何已签发激活码或同步令牌的原文，只会返回不可逆哈希对应的管理记录。
旧版滚动录音没有设备 ID 时，会在管理端统一归为“历史录音”，不会被误判为新设备。
对外部署时必须使用 HTTPS，并限制管理员端仅可从可信网络访问。

模型回放由扫描器优先处理。回放记录会再次下载对应 WAV 并调用记录中的模型 ID；它不会覆盖原任务的当前转写、AI 结果或 MinIO/Obsidian 笔记。若历史自定义 ASR 已被删除或服务不可用，回放会标记失败，原任务不受影响。

macOS 持续运行模板在 `launchd/com.ai-recording-bean.status.plist`。它与
扫描器一样从 `~/Library/Application Support/AI Recording Bean Backend/` 执行，
避免 LaunchAgent 直接执行 Documents 目录中的代码。

## 验证接口

```bash
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
```

安装依赖：

```bash
python3 -m pip install -r backend/requirements.txt
```
