# ASR HTTP 接口合同

当前转写服务：

```text
http://192.168.0.153:10086
```

## 健康检查

```http
GET /health
```

成功响应至少包含：

```json
{
  "status": "ok"
}
```

## 语音转文字

```http
POST /transcribe
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | File | 是 | 待识别 WAV |

成功响应至少包含：

```json
{
  "text": "识别出来的文字"
}
```

后端通过以下环境变量配置服务，不在代码里保存密钥：

```text
ASR_BASE_URL=http://192.168.0.153:10086
ASR_HEALTH_PATH=/health
ASR_TRANSCRIBE_PATH=/transcribe
ASR_TIMEOUT_SECONDS=600
```

适配器实现位置：

```text
backend/app/transcription/local_model_adapter.py
```
