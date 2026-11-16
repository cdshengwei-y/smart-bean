from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.storage.minio_store import MinioRecordingStore


def _settings(provider: str) -> SimpleNamespace:
    return SimpleNamespace(
        minio_endpoint="obs.cn-north-4.myhuaweicloud.com",
        minio_access_key="access",
        minio_secret_key="secret",
        minio_secure=False,
        minio_region="cn-north-4",
        minio_bucket="recordingbean-test",
        recordings_prefix="recordings/",
        notes_prefix="notes/",
        storage_provider=provider,
    )


class MinioStoreTests(unittest.TestCase):
    @patch("app.storage.minio_store.Minio")
    def test_huawei_obs_uses_region_and_virtual_host_endpoint(self, minio_factory):
        client = Mock()
        minio_factory.return_value = client

        MinioRecordingStore(_settings("huawei-obs"))

        minio_factory.assert_called_once_with(
            "obs.cn-north-4.myhuaweicloud.com",
            access_key="access",
            secret_key="secret",
            secure=False,
            region="cn-north-4",
        )
        client.enable_virtual_style_endpoint.assert_called_once_with()

    @patch("app.storage.minio_store.Minio")
    def test_minio_does_not_enable_huawei_virtual_host_mode(self, minio_factory):
        client = Mock()
        minio_factory.return_value = client

        MinioRecordingStore(_settings("minio"))

        client.enable_virtual_style_endpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
