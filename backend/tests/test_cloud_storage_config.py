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

from app.cloud_storage_config import (
    load_cloud_storage_config,
    prepare_cloud_storage_config,
    save_cloud_storage_config,
)
from app.config import ScannerSettings
from status_server import StatusHandler


class _FakeStore:
    def list_recordings(self):
        return [object(), object()]

    def list_notes(self, prefix=None):
        return [object()]


class CloudStorageConfigTests(unittest.TestCase):
    def test_s3_config_masks_credentials_and_reloads_scanner_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud-storage.json"
            with patch.dict(
                os.environ,
                {"CLOUD_STORAGE_CONFIG_PATH": str(path)},
                clear=False,
            ):
                public = save_cloud_storage_config(
                    {
                        "type": "s3",
                        "provider": "tencent-cos",
                        "endpoint": "https://cos.example.test",
                        "region": "ap-beijing",
                        "bucket": "recording-bean",
                        "recordings_prefix": "audio",
                        "notes_prefix": "transcripts",
                        "access_key": "private-access",
                        "secret_key": "private-secret",
                        "tls_ca_cert": "-----BEGIN CERTIFICATE-----\nexample\n-----END CERTIFICATE-----",
                    },
                    owner_user_id=7,
                )
                settings = ScannerSettings.load()
                stored = load_cloud_storage_config()

            self.assertTrue(public["access_key_configured"])
            self.assertTrue(public["secret_key_configured"])
            self.assertEqual(public["access_key"], "")
            self.assertEqual(public["secret_key"], "")
            self.assertEqual(settings.minio_endpoint, "cos.example.test")
            self.assertTrue(settings.minio_secure)
            self.assertEqual(settings.minio_bucket, "recording-bean")
            self.assertEqual(settings.recordings_prefix, "audio/")
            self.assertEqual(settings.notes_prefix, "transcripts/")
            self.assertEqual(settings.minio_region, "ap-beijing")
            self.assertEqual(stored["secret_key"], "private-secret")
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_custom_adapter_options_are_preserved_for_the_backend_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud-storage.json"
            with patch.dict(
                os.environ,
                {"CLOUD_STORAGE_CONFIG_PATH": str(path)},
                clear=False,
            ):
                public = save_cloud_storage_config(
                    {
                        "type": "custom",
                        "adapter_factory": "tests.test_cloud_storage_config:create_store",
                        "adapter_options_json": json.dumps(
                            {"tenant": "private-tenant", "token": "private-token"}
                        ),
                    },
                    owner_user_id=7,
                )
                settings = ScannerSettings.load()

            self.assertEqual(public["type"], "custom")
            self.assertTrue(public["adapter_options_configured"])
            self.assertEqual(public["adapter_options_json"], "")
            self.assertEqual(
                settings.storage_factory,
                "tests.test_cloud_storage_config:create_store",
            )
            self.assertEqual(settings.storage_options["tenant"], "private-tenant")
            self.assertNotIn("private-token", json.dumps(public))

    def test_empty_mobile_credentials_keep_the_existing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cloud-storage.json"
            with patch.dict(
                os.environ,
                {"CLOUD_STORAGE_CONFIG_PATH": str(path)},
                clear=False,
            ):
                save_cloud_storage_config(
                    {
                        "type": "s3",
                        "provider": "minio",
                        "endpoint": "http://minio.example.test:9000",
                        "region": "us-east-1",
                        "bucket": "recordingbean",
                        "access_key": "old-access",
                        "secret_key": "old-secret",
                    }
                )
                prepared = prepare_cloud_storage_config(
                    {
                        "type": "s3",
                        "provider": "minio",
                        "endpoint": "http://new-minio.example.test:9000",
                        "region": "us-east-1",
                        "bucket": "recordingbean",
                    }
                )

            self.assertEqual(prepared["access_key"], "old-access")
            self.assertEqual(prepared["secret_key"], "old-secret")

    def test_storage_api_requires_login_and_never_returns_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "cloud-storage.json"

            class Handler(StatusHandler):
                database_path = root / "jobs.db"

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=3
            )
            with patch.dict(
                os.environ,
                {
                    "CLOUD_STORAGE_CONFIG_PATH": str(config_path),
                    "MINIO_ENDPOINT": "http://minio.example.test:9000",
                    "MINIO_ACCESS_KEY": "backend-access",
                    "MINIO_SECRET_KEY": "backend-secret",
                    "MINIO_BUCKET": "recordingbean",
                },
                clear=False,
            ):
                try:
                    connection.request("GET", "/api/config/storage")
                    unauthorized = connection.getresponse()
                    self.assertEqual(unauthorized.status, 401)
                    unauthorized.read()

                    connection.request(
                        "POST",
                        "/api/auth/register",
                        body=json.dumps(
                            {"email": "owner@example.com", "password": "passw0rd!"}
                        ).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    registration = connection.getresponse()
                    token = json.loads(registration.read())["token"]
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer %s" % token,
                    }

                    connection.request(
                        "GET",
                        "/api/config/storage",
                        headers={"Authorization": "Bearer %s" % token},
                    )
                    default_response = connection.getresponse()
                    default_payload = json.loads(default_response.read())
                    self.assertEqual(default_response.status, 200)
                    self.assertEqual(
                        default_payload["storage"]["source"],
                        "backend-default",
                    )
                    self.assertTrue(
                        default_payload["storage"]["uses_backend_default"]
                    )
                    self.assertNotIn("backend-secret", json.dumps(default_payload))

                    with patch(
                        "status_server.load_recording_store",
                        return_value=_FakeStore(),
                    ):
                        connection.request(
                            "POST",
                            "/api/config/storage/test",
                            body=json.dumps(
                                {"use_backend_default": True}
                            ).encode("utf-8"),
                            headers=headers,
                        )
                        default_test = connection.getresponse()
                        default_test_payload = json.loads(default_test.read())
                    self.assertEqual(default_test.status, 200)
                    self.assertTrue(
                        default_test_payload["storage"]["uses_backend_default"]
                    )
                    self.assertEqual(default_test_payload["recordings_count"], 2)

                    body = json.dumps(
                        {
                            "type": "s3",
                            "provider": "aliyun-oss",
                            "endpoint": "https://oss.example.test",
                            "region": "cn-hangzhou",
                            "bucket": "recording-bean",
                            "recordings_prefix": "recordings/",
                            "notes_prefix": "notes/",
                            "access_key": "private-access",
                            "secret_key": "private-secret",
                        }
                    ).encode("utf-8")
                    connection.request(
                        "POST",
                        "/api/config/storage",
                        body=body,
                        headers=headers,
                    )
                    saved = connection.getresponse()
                    saved_payload = json.loads(saved.read())
                    self.assertEqual(saved.status, 200)
                    self.assertTrue(
                        saved_payload["storage"]["secret_key_configured"]
                    )
                    self.assertNotIn("private-secret", json.dumps(saved_payload))

                    with patch(
                        "status_server.load_recording_store",
                        return_value=_FakeStore(),
                    ):
                        connection.request(
                            "POST",
                            "/api/config/storage/test",
                            body=json.dumps(
                                {
                                    "type": "s3",
                                    "provider": "aliyun-oss",
                                    "endpoint": "https://oss.example.test",
                                    "region": "cn-hangzhou",
                                    "bucket": "recording-bean",
                                }
                            ).encode("utf-8"),
                            headers=headers,
                        )
                        tested = connection.getresponse()
                        tested_payload = json.loads(tested.read())
                    self.assertEqual(tested.status, 200)
                    self.assertEqual(tested_payload["recordings_count"], 2)
                    self.assertEqual(tested_payload["notes_count"], 1)
                    self.assertNotIn("private-access", json.dumps(tested_payload))

                    connection.request(
                        "POST",
                        "/api/config/storage",
                        body=json.dumps(
                            {"use_backend_default": True}
                        ).encode("utf-8"),
                        headers=headers,
                    )
                    reset = connection.getresponse()
                    reset_payload = json.loads(reset.read())
                    self.assertEqual(reset.status, 200)
                    self.assertTrue(
                        reset_payload["storage"]["uses_backend_default"]
                    )
                    self.assertFalse(config_path.exists())
                finally:
                    connection.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=3)


def create_store(settings: ScannerSettings) -> _FakeStore:
    return _FakeStore()


if __name__ == "__main__":
    unittest.main()
