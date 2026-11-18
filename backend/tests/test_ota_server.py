from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from ota_server import OtaHandler


class OtaServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def test_apk_download_accepts_downloader_query_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.bin"
            apk = root / "app.apk"
            firmware.write_bytes(b"firmware")
            apk.write_bytes(b"android-package")
            OtaHandler.firmware_path = firmware
            OtaHandler.apk_path = apk
            OtaHandler.version = "0.3.5"
            OtaHandler.app_version = "0.4.6"
            OtaHandler.firmware_url = "http://127.0.0.1/firmware/0.3.5.bin"
            OtaHandler.device_firmwares = {}
            OtaHandler.apk_url = "http://127.0.0.1/app/0.4.6.apk"
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), OtaHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            url = (
                f"http://127.0.0.1:{self.server.server_port}/app/0.4.6.apk"
                "?miuiStable=1&client_version_code=202410100"
            )
            with urlopen(url, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"android-package")

    def test_manifest_and_release_describe_the_same_firmware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "firmware.bin"
            firmware.write_bytes(b"firmware")
            OtaHandler.firmware_path = firmware
            OtaHandler.apk_path = None
            OtaHandler.version = "0.3.11"
            OtaHandler.app_version = "0.5.32+56"
            OtaHandler.firmware_url = "http://127.0.0.1/firmware/0.3.11.bin"
            OtaHandler.device_firmwares = {}
            OtaHandler.apk_url = ""
            OtaHandler.ios_app_url = ""
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), OtaHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()

            with urlopen(
                f"http://127.0.0.1:{self.server.server_port}/manifest.json",
                timeout=3,
            ) as response:
                manifest = json.loads(response.read())
            with urlopen(
                f"http://127.0.0.1:{self.server.server_port}/release.json",
                timeout=3,
            ) as response:
                release = json.loads(response.read())

            self.assertEqual(manifest["version"], "0.3.11")
            self.assertEqual(manifest["url"], release["firmware"]["url"])
            self.assertEqual(release["firmware"]["version"], "0.3.11")
            # 未配置 APK 时 app 字段为 None（部署版语义：有 apk_url 才有 app）
            self.assertIsNone(release["app"])


if __name__ == "__main__":
    unittest.main()
