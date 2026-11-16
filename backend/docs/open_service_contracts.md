# 开放服务接口

本项目默认仍可使用 MinIO、智谱 GLM-ASR 与内置同步服务。本文件定义
可替换的后端服务合同，用户无需把对象存储或 ASR 密钥写入手机 App、Obsidian 插件或
设备外的公开配置。

## 1. 自定义后端存储服务

### S3 兼容服务

设备固件使用 AWS Signature V4 的 path-style S3 上传，因此兼容 MinIO 的服务可直接
替换。配置 `RECORDER_MINIO_ENDPOINT`、`RECORDER_MINIO_BUCKET`、Access Key 和
Secret Key 后，录音会继续写入：

```text
recordings/<device_id>/<filename>.wav
```

后端默认的 `STORAGE_FACTORY=app.storage.minio_store:create_store` 同样适用于这类服务。
`STORAGE_BUCKET`、`STORAGE_RECORDINGS_PREFIX` 与 `STORAGE_NOTES_PREFIX` 是通用名称；
旧的 `MINIO_*` 前缀仍兼容。

设备端不能直接上传到非 S3 协议的服务。此类服务需要在设备与服务之间部署 S3 兼容网关，
或由自定义存储适配器从已有录音源读取数据。手机选择“自定义适配器”时只切换后端读取和
写入逻辑，不会把 Python 适配器代码下载到录音豆。

### 手机配置接口

已登录的手机 App 使用以下接口管理对象存储：

```text
GET  /api/config/storage
POST /api/config/storage/test
POST /api/config/storage
```

请求必须带账户 Bearer Token。S3 兼容配置的 JSON 字段为：

```json
{
  "type": "s3",
  "provider": "minio",
  "endpoint": "http://192.168.0.111:30900",
  "region": "us-east-1",
  "bucket": "recordingbean",
  "recordings_prefix": "recordings/",
  "notes_prefix": "notes/",
  "access_key": "填写在手机里的 AK",
  "secret_key": "填写在手机里的 SK",
  "tls_ca_cert": "HTTPS 设备直连所需的 CA PEM"
}
```

`provider` 可选 `minio`、`aliyun-oss`、`huawei-obs`、`tencent-cos` 或
`other-s3`。后端返回的 `access_key`、`secret_key` 永远为空，只返回
`access_key_configured` 和 `secret_key_configured`。保存时如果手机把密钥留空，后端会
保留原密钥；要换云厂商时重新填写新的 AK/SK。

默认 MinIO 不需要在手机填写密钥。后端会从既有的 `MINIO_*` 配置或
`minio_config.json` 读取它，并在响应中标记 `uses_backend_default: true`。手机测试或
恢复这套默认存储时，只发送：

```json
{
  "use_backend_default": true
}
```

这个请求不会读取、返回或下发后端默认 AK/SK；保存时会移除此前的用户自定义对象存储
覆盖配置，恢复后端默认存储。只有用户主动切换到自定义 S3 服务时，手机才会要求填写并
保存该用户自己的 AK/SK。

自定义适配器请求使用：

```json
{
  "type": "custom",
  "adapter_factory": "package.module:create_store",
  "adapter_options_json": "{\"tenant\":\"example\"}"
}
```

`adapter_options_json` 只保存在后端，公开响应只返回
`adapter_options_configured`。自定义适配器需要自行实现下方的 `RecordingStore` 合同。
它不能被录音豆直接执行；如果录音豆仍需上传，设备侧必须配置一个 S3 兼容接收端。

`/api/config/storage/test` 会用请求配置临时创建适配器，或在
`use_backend_default=true` 时直接使用后端默认存储，并分别读取录音列表和笔记列表；不会
写入测试对象。保存接口成功后，后端的同步 API 会在下一次请求直接加载新配置，扫描器和
AI Worker 会在下一轮循环热重载对象存储适配器。

录音豆通过手机蓝牙接收 S3 配置，协议为：

```text
STORAGE:BEGIN:<base64长度>
STORAGE:CHUNK:<序号>:<base64数据>
STORAGE:COMMIT
```

`COMMIT` 只接受 `type=s3`，设备会校验 Endpoint、Bucket、AK/SK 和 HTTPS CA PEM，
然后以版本号写入 NVS。存储切换后，设备会清理本地上传标记，让 TF 卡中已有 WAV 可以
重新上传到新对象存储。HTTPS 分片上传使用 `WiFiClientSecure` 和用户提供的 CA，不会
使用 `setInsecure()`。

后端默认模式不会经由蓝牙下发密钥，录音豆继续使用固件或既有 NVS 中的默认存储配置。
自定义 S3 模式才会把用户刚填写的 AK/SK 同步到录音豆。

### 自定义适配器

将 `STORAGE_FACTORY` 设置为 `package.module:create_store`。工厂接收一个
`ScannerSettings` 实例，并返回实现以下方法的对象：

```python
def create_store(settings):
    return Store()

class Store:
    def list_recordings(self) -> list[RecordingObject]: ...
    def download_recording(self, object_name: str, destination: Path) -> None: ...
    def note_object_name(self, recording_object_name: str) -> str: ...
    def upload_note(self, object_name: str, markdown: str) -> None: ...
    def list_notes(self, prefix: str | None = None) -> list[NoteObject]: ...
    def download_note(self, object_name: str) -> str: ...
```

`RecordingObject` 与 `NoteObject` 定义在
`backend/app/storage/contract.py`，均包含 `object_name`、`etag`、`size` 和
`last_modified`。`etag` 必须在同一对象内容变化时变化，否则去重、增量同步和重处理无法
正确工作。

## 2. 自定义后端转写服务

### 通用 HTTP 转写

启用：

```text
CUSTOM_ASR_ENABLED=1
CUSTOM_ASR_BASE_URL=https://asr.example.com
```

后端会向 `POST <CUSTOM_ASR_BASE_URL><CUSTOM_ASR_TRANSCRIBE_PATH>` 发出
`multipart/form-data` 请求：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `file` | `CUSTOM_ASR_FILE_FIELD=file` | WAV 文件，Content-Type 为 `audio/wav` |
| `language` | `CUSTOM_ASR_LANGUAGE_FIELD=language` | 可选，默认 `zh` |
| `prompt` | `CUSTOM_ASR_PROMPT_FIELD=prompt` | 可选识别提示 |

成功响应必须为 JSON，默认读取字段：

```json
{
  "text": "完整转写文本",
  "language": "zh",
  "model": "provider-model",
  "segments": [
    {"start": 0, "end": 1.2, "text": "可选分段文本", "speaker": "可选"}
  ]
}
```

可用 `CUSTOM_ASR_RESPONSE_*_PATH` 指定嵌套字段，例如
`CUSTOM_ASR_RESPONSE_TEXT_PATH=result.transcript`。可通过
`CUSTOM_ASR_API_KEY`、`CUSTOM_ASR_API_KEY_HEADER`、`CUSTOM_ASR_API_KEY_PREFIX` 和
`CUSTOM_ASR_HEADERS_JSON` 配置认证头。设置 `CUSTOM_ASR_HEALTH_PATH=` 可跳过健康检查。

启用后，状态 API 和 Flutter App 会额外显示 `custom-asr:<服务编号>`，用户可选择它处理后续录音。

手机 App 已提供“ASR服务 → 自定义服务”配置入口，可新增、编辑、停用或删除多个服务。
填写服务名称、服务说明、服务地址、转写路径、字段名和可选 API Key 后，App 会调用
`POST /api/config/asr` 保存配置并自动切换到这个服务。首次保存时后端生成服务编号；
更新时在请求中带回该 `id`。可用 `DELETE /api/config/asr/<id>` 删除一条服务，删除当前
选中的服务会自动回退到默认模型。

服务保存在后端 `custom_asr.json`（权限为当前服务用户可读写）。旧的单条配置文件会自动
作为第一条服务迁移；API Key 按服务单独保存，状态接口和手机回显只返回
`api_key_configured`，不会返回密钥正文。状态响应的 `asr.custom_asrs` 是全部自定义
服务的公开配置数组。手机与状态服务不在同一可信局域网时必须使用 HTTPS。

手机端为常用字段提供了选择菜单，例如转写文字字段可选 `text`、`markdown`、`transcript`
或嵌套路径；输入框仍可手动填写任意符合本合同的字段名或 JSON 点路径。

### Qwen3-ASR 配置示例

当前局域网的 Qwen3-ASR 服务可直接按通用 HTTP 转写配置填写：

| 手机字段 | 值 |
| --- | --- |
| 服务名称 | 千问 Qwen3-ASR |
| 服务地址 | `http://192.168.0.153:18005` |
| 转写接口路径 | `/transcribe` |
| 健康检查路径 | `/health` |
| 请求超时（秒） | `600` |
| 音频字段名 | `file` |
| 语言字段名 | `language` |
| 转写文字字段 | `text` |
| 语言字段 | `language` |
| 模型字段 | `model` |

该服务不需要 API Key；提示词字段、分段字段和自定义请求头可留空。保存并使用后，录制一段短音频，状态变为“已完成”且笔记出现转写文字，即为配置通过。

### 复杂转写服务

异步任务、WebSocket、厂商 SDK 或非上述 JSON 格式时，将
`CUSTOM_ASR_FACTORY` 设为自己的 `package.module:create_transcriber`。工厂必须返回
符合 `backend/app/transcription/contract.py` 中 `Transcriber` 合同的对象：

```python
def create_transcriber():
    return MyTranscriber()

class MyTranscriber:
    provider_name = "customer-asr"

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return TranscriptionResult(text="完整转写", language="zh", model_name=self.provider_name)
```

可选实现 `health()`，用于状态页在线检测；也可实现
`transcribe_with_progress(request, on_progress=...)` 以生成长录音的阶段性笔记。

## 3. Obsidian 插件同步服务接口

插件配置“同步服务地址”“同步接口前缀”以及激活、列表、下载三个相对接口路径，默认请求：

```text
POST <服务地址>/<接口前缀>/activate
GET  <服务地址>/<接口前缀>/notes
GET  <服务地址>/<接口前缀>/notes/<id>
```

默认接口前缀为 `api/sync`，三个接口路径默认是 `activate`、`notes`、`notes/{id}`。
服务地址可以带部署路径，例如
`https://sync.example.com/recording-bean`；插件不会接受查询参数、锚点、用户名或密码。
接口前缀必须是相对路径，不能包含 `..`。

### 激活

```http
POST /api/sync/activate
Content-Type: application/json

{"activation_code":"ARB-...","client_label":"Obsidian: Vault Name"}
```

成功响应：

```json
{"device_id":"bean-a1b2c3","sync_token":"<至少 32 字符>","token_type":"Bearer"}
```

### 列出完成笔记

```http
GET /api/sync/notes
Authorization: Bearer <sync_token>
```

成功响应：

```json
{
  "device_id": "bean-a1b2c3",
  "notes": [
    {
      "id": 42,
      "path": "2026/REC_001.md",
      "etag": "content-version",
      "updated_at": "2026-08-14T12:00:00+00:00"
    }
  ]
}
```

`path` 必须是相对 `.md` 路径，不能以 `/` 开头或包含 `..`。插件将其写入：

```text
<Vault>/AI录音豆/<device_id>/<path>
```

### 下载单篇笔记

```http
GET /api/sync/notes/42
Authorization: Bearer <sync_token>
Accept: application/json
```

成功响应为 UTF-8 Markdown 正文，`Content-Type` 建议设置为
`text/markdown; charset=utf-8`。插件以 `id + etag` 增量同步，只覆盖本插件管理目录中的
同名 Markdown，绝不删除 Vault 的其他文件。

生产环境必须使用 HTTPS。同步令牌属于设备私密凭证，服务端应校验其设备范围，并且只能返回
已完成的最终 Markdown，不能返回对象存储密钥、原始 WAV 或未完成的转写文本。

### 手机切换输出电脑

已登录的手机 App 可调用：

```text
POST /api/mobile/recorder-binding
GET  /api/mobile/obsidian-sync
POST /api/mobile/obsidian-sync/activation
```

手机在后台用真实 `device_id` 调用 `recorder-binding`，为账户绑定唯一的一台录音豆。随后
`GET /api/mobile/obsidian-sync` 会返回 `bound` 与当前输出电脑；激活接口只需提交可选
`target_name`，不接收客户端指定的设备 ID。它返回 15 分钟内有效的一次性激活码。手机会把
服务地址和激活码组合为 `recording-bean://obsidian-sync?...` 配对信息；新版插件可直接导入，
也可分别填写服务地址和激活码。新电脑激活成功时，服务端会在同一事务中撤销该账户录音豆的所有
旧同步令牌并创建新令牌，因此输出只会继续同步到新电脑；尚未激活的配对码不会中断旧电脑同步。
