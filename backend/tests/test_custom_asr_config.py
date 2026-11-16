from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app.database import JobRepository
from app.transcription.custom_config import (
    load_custom_asr_config,
    load_custom_asr_configs,
    public_custom_asr_config,
    save_custom_asr_config,
)
from app.transcription.model_registry import CUSTOM_ASR_PREFIX, ModelRegistry, ZHIPU_GLM_ASR
from status_server import StatusHandler


class CustomAsrConfigTests(unittest.TestCase):
    def test_saved_config_masks_api_key_and_is_available_to_model_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "custom-asr.json"
            with patch.dict(
                os.environ,
                {"CUSTOM_ASR_CONFIG_PATH": str(config_path)},
                clear=False,
            ):
                public = save_custom_asr_config(
                    {
                        "enabled": True,
                        "name": "客户 ASR",
                        "description": "客户服务",
                        "base_url": "https://asr.example.test",
                        "transcribe_path": "/v1/transcribe",
                        "api_key": "private-key",
                    }
                )
                registry = ModelRegistry(Path(directory) / "selected.json")

                self.assertTrue(public["enabled"])
                self.assertTrue(public["api_key_configured"])
                self.assertEqual(public["api_key"], "")
                self.assertEqual(load_custom_asr_config()["api_key"], "private-key")
                self.assertIn(
                    CUSTOM_ASR_PREFIX + public["id"],
                    {model.id for model in registry.models()},
                )

    def test_legacy_single_provider_is_migrated_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "custom-asr.json"
            config_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "name": "旧配置",
                        "base_url": "https://asr.example.test",
                        "api_key": "private-key",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"CUSTOM_ASR_CONFIG_PATH": str(config_path)},
                clear=False,
            ):
                loaded = load_custom_asr_configs()
                self.assertEqual(loaded[0]["id"], "default")
                self.assertEqual(loaded[0]["api_key"], "private-key")
                saved = save_custom_asr_config(
                    {"id": "default", "name": "迁移后的配置"}
                )
                payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["name"], "迁移后的配置")
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["providers"][0]["id"], "default")
        self.assertEqual(payload["providers"][0]["api_key"], "private-key")

    def test_status_api_saves_and_selects_custom_asr_without_returning_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = JobRepository(root / "jobs.db")
            repository.close()
            with patch.dict(
                os.environ,
                {"CUSTOM_ASR_CONFIG_PATH": str(root / "custom-asr.json")},
                clear=False,
            ):
                class Handler(StatusHandler):
                    model_config_path = root / "selected.json"
                    database_path = root / "jobs.db"

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=3
                )
                try:
                    body = json.dumps(
                        {
                            "enabled": True,
                            "name": "手机配置 ASR",
                            "description": "手机写入",
                            "base_url": "https://asr.example.test",
                            "transcribe_path": "/transcribe",
                            "api_key": "private-key",
                            "select": True,
                        }
                    ).encode("utf-8")
                    connection.request(
                        "POST",
                        "/api/config/asr",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                    provider_id = payload["custom_asr"]["id"]
                    model_id = CUSTOM_ASR_PREFIX + provider_id
                    self.assertEqual(payload["asr"]["selected_model_id"], model_id)
                    self.assertTrue(payload["custom_asr"]["api_key_configured"])
                    self.assertEqual(payload["custom_asr"]["api_key"], "")
                    self.assertNotIn("private-key", json.dumps(payload))

                    connection.request(
                        "POST",
                        "/api/config/asr",
                        body=json.dumps(
                            {
                                "enabled": True,
                                "name": "第二个 ASR",
                                "base_url": "https://second.example.test",
                                "transcribe_path": "/asr",
                                "select": False,
                            }
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    second_response = connection.getresponse()
                    second_payload = json.loads(second_response.read())
                    self.assertEqual(second_response.status, 200)
                    self.assertEqual(len(second_payload["custom_asrs"]), 2)

                    connection.request("GET", "/api/status")
                    status_response = connection.getresponse()
                    status_payload = json.loads(status_response.read())
                    self.assertEqual(status_response.status, 200)
                    self.assertEqual(
                        status_payload["asr"]["selected_model_id"],
                        model_id,
                    )
                    self.assertEqual(
                        status_payload["asr"]["custom_asrs"][0]["name"],
                        "手机配置 ASR",
                    )
                    self.assertEqual(
                        len(status_payload["asr"]["custom_asrs"]),
                        2,
                    )

                    connection.request(
                        "DELETE",
                        "/api/config/asr/%s" % provider_id,
                    )
                    delete_response = connection.getresponse()
                    delete_payload = json.loads(delete_response.read())
                    self.assertEqual(delete_response.status, 200)
                    self.assertEqual(
                        delete_payload["asr"]["selected_model_id"],
                        ZHIPU_GLM_ASR,
                    )
                    self.assertEqual(len(delete_payload["custom_asrs"]), 1)
                finally:
                    connection.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
