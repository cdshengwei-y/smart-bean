from __future__ import annotations

import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.transcription.contract import TranscriptionRequest
from app.transcription.custom_http_adapter import CustomHttpTranscriber


class CustomHttpAdapterTests(unittest.TestCase):
    def test_uses_configured_multipart_contract_and_normalizes_response(self) -> None:
        class Response:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {
                    "result": {
                        "transcript": "自定义服务转写成功。",
                        "language": "zh",
                        "model": "customer-asr-v1",
                        "segments": [
                            {"start": 0, "end": 1.25, "text": "自定义服务转写成功。"}
                        ],
                    }
                }

        class Session:
            def post(self, url, files, data, headers, timeout):
                self.url = url
                self.files = files
                self.data = data
                self.headers = headers
                self.timeout = timeout
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "sample.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 16000)
            session = Session()
            with patch.dict(
                os.environ,
                {
                    "CUSTOM_ASR_RESPONSE_TEXT_PATH": "result.transcript",
                    "CUSTOM_ASR_RESPONSE_LANGUAGE_PATH": "result.language",
                    "CUSTOM_ASR_RESPONSE_MODEL_PATH": "result.model",
                    "CUSTOM_ASR_RESPONSE_SEGMENTS_PATH": "result.segments",
                    "CUSTOM_ASR_API_KEY": "test-key",
                    "CUSTOM_ASR_HEADERS_JSON": '{"X-Tenant": "customer-a"}',
                },
                clear=False,
            ):
                result = CustomHttpTranscriber(
                    base_url="https://asr.example.test",
                    session=session,
                ).transcribe(
                    TranscriptionRequest(
                        audio_path=audio_path,
                        language="zh",
                        prompt="会议纪要",
                    )
                )

        self.assertEqual(session.url, "https://asr.example.test/transcribe")
        self.assertEqual(session.data, {"language": "zh", "prompt": "会议纪要"})
        self.assertEqual(session.files["file"][0], "sample.wav")
        self.assertEqual(session.headers["Authorization"], "Bearer test-key")
        self.assertEqual(session.headers["X-Tenant"], "customer-a")
        self.assertEqual(result.text, "自定义服务转写成功。")
        self.assertEqual(result.model_name, "customer-asr-v1")
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.segments[0].end_seconds, 1.25)
