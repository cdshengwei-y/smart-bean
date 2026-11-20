from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from app.fetcher import DeviceFetcher

SAMPLE_WAV = b"RIFF" + b"\x00" * 40 + b"audio-data-here"


class FakeDeviceHandler(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {"REC_20261120_100000_0.wav": SAMPLE_WAV}

    def do_GET(self) -> None:
        if self.path == "/files":
            payload = json.dumps(
                {
                    "files": [
                        {"name": name, "size": len(body)}
                        for name, body in FakeDeviceHandler.files.items()
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/files/"):
            name = self.path[len("/files/"):]
            body = FakeDeviceHandler.files.get(name)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args) -> None:  # 静默
        pass


class DeviceFetcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDeviceHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self._tmp.cleanup()

    def _fetcher(self) -> DeviceFetcher:
        from app.config import ScannerSettings

        settings = ScannerSettings(
            minio_endpoint="127.0.0.1:9000",
            minio_access_key="test",
            minio_secret_key="test-secret",
            minio_secure=False,
            minio_bucket="recordingbean",
            recordings_prefix="recordings/",
            notes_prefix="notes/",
            database_path=self.root / "jobs.db",
            scan_interval_seconds=15,
            max_attempts=3,
            retry_delay_seconds=60,
            transcriber_factory="unused",
            transcription_language="zh",
        )
        return DeviceFetcher(settings, device_base_url=self.base, device_id="bean_ye_2")

    def test_fetches_new_file_into_archive(self) -> None:
        fetcher = self._fetcher()
        fetched = fetcher.poll_once()
        self.assertEqual(fetched, 1)
        archive = (
            self.root
            / "recordings_archive"
            / "recordings"
            / "bean_ye_2"
            / "REC_20261120_100000_0.wav"
        )
        self.assertTrue(archive.is_file())
        self.assertEqual(archive.read_bytes(), SAMPLE_WAV)
        # 状态已记录：再次轮询不重复拉取
        self.assertEqual(fetcher.poll_once(), 0)

    def test_re_fetches_when_size_grows(self) -> None:
        fetcher = self._fetcher()
        fetcher.poll_once()
        # 文件增长（模拟录音进行中的 .part）：重新拉取
        FakeDeviceHandler.files["REC_20261120_100000_0.wav"] = SAMPLE_WAV + b"more"
        fetched = fetcher.poll_once()
        self.assertEqual(fetched, 1)
        archive = (
            self.root / "recordings_archive" / "recordings" / "bean_ye_2" / "REC_20261120_100000_0.wav"
        )
        self.assertEqual(archive.read_bytes(), SAMPLE_WAV + b"more")


if __name__ == "__main__":
    unittest.main()
