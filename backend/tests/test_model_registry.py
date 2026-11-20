from __future__ import annotations

import tempfile
import unittest
import json
from os import environ
from pathlib import Path
from unittest.mock import patch

from app.transcription.contract import TranscriptionRequest
from app.transcription.custom_config import save_custom_asr_config
from app.transcription.model_registry import CUSTOM_ASR_PREFIX, ModelRegistry
from app.transcription.qwen_asr_adapter import QwenAsrTranscriber


class ModelRegistryTests(unittest.TestCase):
    def test_zhipu_glm_asr_is_removed_from_builtin_models(self) -> None:
        """智谱 GLM-ASR 服务已于 2026-11-20 废弃下线（用户要求删除）。"""
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory) / "asr_model.json")
            model_ids = {model.id for model in registry.models()}
            self.assertNotIn("zhipu-glm-asr", model_ids)
            with self.assertRaisesRegex(ValueError, "不支持的转写模型"):
                registry.select("zhipu-glm-asr")

    def test_qwen_is_not_an_builtin_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = ModelRegistry(Path(directory) / "asr_model.json")
            model_ids = {model.id for model in registry.models()}
            self.assertNotIn("qwen3-asr", model_ids)
            with self.assertRaisesRegex(ValueError, "不支持的转写模型"):
                registry.select("qwen3-asr")

    def test_custom_http_model_is_selectable_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                environ,
                {
                    "CUSTOM_ASR_CONFIG_PATH": str(Path(directory) / "custom-asr.json"),
                },
                clear=False,
            ):
                config = save_custom_asr_config(
                    {
                        "enabled": True,
                        "name": "客户 ASR",
                        "base_url": "https://asr.example.test",
                    }
                )
                registry = ModelRegistry(Path(directory) / "asr_model.json")
                model_id = CUSTOM_ASR_PREFIX + config["id"]
                selected = registry.select(model_id)

        models = {model["id"]: model for model in selected["models"]}
        self.assertEqual(selected["selected_model_id"], model_id)
        self.assertEqual(models[model_id]["name"], "客户 ASR")

    def test_build_model_accepts_legacy_qwen_response_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            custom_config_path = Path(directory) / "custom-asr.json"
            with patch.dict(
                environ,
                {
                    "CUSTOM_ASR_CONFIG_PATH": str(custom_config_path),
                },
                clear=False,
            ):
                custom_config_path.write_text(
                    json.dumps(
                        {
                            "enabled": True,
                            "name": "千问 Qwen3-ASR",
                            "base_url": "https://qwen.example.test",
                        }
                    ),
                    encoding="utf-8",
                )
                registry = ModelRegistry(Path(directory) / "asr_model.json")
                with patch.object(
                    registry,
                    "_build_custom_transcriber",
                    return_value=object(),
                ) as build_custom:
                    registry.build_model("Qwen3-ASR-1.7B")

        build_custom.assert_called_once()


class QwenAdapterTests(unittest.TestCase):
    def test_uses_the_service_multipart_contract(self) -> None:
        class Response:
            ok = True
            status_code = 200
            text = ""

            def json(self):
                return {"text": "测试转写", "language": "Chinese", "model": "Qwen3-ASR-1.7B"}

        class Session:
            def post(self, url, files, data, timeout):
                self.url = url
                self.files = files
                self.data = data
                self.timeout = timeout
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "sample.wav"
            import wave

            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 160)
            session = Session()
            result = QwenAsrTranscriber(base_url="http://qwen.test", session=session).transcribe(
                TranscriptionRequest(audio_path=audio_path)
            )

        self.assertEqual(session.url, "http://qwen.test/transcribe")
        self.assertEqual(session.data, {"language": "zh"})
        self.assertEqual(result.text, "测试转写")
        self.assertEqual(result.model_name, "Qwen3-ASR-1.7B")

    def test_chunk_progress_reports_ordered_prefix_text(self) -> None:
        class Response:
            ok = True
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Session:
            def __init__(self) -> None:
                self.filenames = []

            def post(self, url, files, data, timeout):
                filename = files["file"][0]
                self.filenames.append(filename)
                if filename == "chunk_000.wav":
                    return Response({"text": "第一段文字", "language": "zh", "model": "qwen"})
                if filename == "chunk_001.wav":
                    return Response({"text": "第二段文字", "language": "zh", "model": "qwen"})
                return Response({"text": "", "language": "zh", "model": "qwen"})

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "long.wav"
            import wave

            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(10)
                audio.writeframes(b"\x00\x00" * 25)
            session = Session()
            progress = []
            result = QwenAsrTranscriber(
                base_url="http://qwen.test",
                chunk_seconds=1,
                session=session,
            ).transcribe_with_progress(
                TranscriptionRequest(audio_path=audio_path),
                on_progress=progress.append,
            )

        self.assertEqual(result.text, "第一段文字\n\n第二段文字")
        self.assertTrue(progress)
        self.assertEqual(progress[0].text, "第一段文字")
        self.assertIn("第二段文字", progress[-1].text)
        self.assertEqual(progress[-1].metadata["completed_chunks"], "3")
        self.assertEqual(progress[-1].metadata["total_chunks"], "3")
