#!/usr/bin/env python3
"""Serve a paired Android/firmware release on the local network.

This is a development server for device verification. It intentionally has no
authentication or cryptographic verification and must not be exposed publicly.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class OtaHandler(BaseHTTPRequestHandler):
    firmware_path: Path
    apk_path: Path | None
    version: str
    app_version: str = ""
    firmware_url: str
    device_firmwares: dict[str, tuple[Path, str]] = {}
    apk_url: str = ""
    ios_app_url: str = ""

    def do_GET(self) -> None:
        request_path = urlparse(self.path).path
        if request_path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if request_path == "/manifest.json":
            self._send_json(HTTPStatus.OK, {"version": self.version, "url": self.firmware_url})
            return
        if request_path.startswith("/manifest/") and request_path.endswith(".json"):
            device_id = request_path.removeprefix("/manifest/").removesuffix(".json")
            item = self.device_firmwares.get(device_id)
            if item is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown device"})
            else:
                _, url = item
                self._send_json(HTTPStatus.OK, {"version": self.version, "url": url})
            return
        if request_path == "/release.json":
            self._send_json(
                HTTPStatus.OK,
                {
                    "version": self.version,
                    "firmware": {"version": self.version, "url": self.firmware_url},
                    "app": {
                        "version": self.app_version,
                        "url": self.apk_url,
                        "ios_url": self.ios_app_url,
                    },
                },
            )
            return
        if request_path == urlparse(self.firmware_url).path:
            self._send_firmware()
            return
        for path, url in self.device_firmwares.values():
            if request_path == urlparse(url).path:
                self._send_file(path, "application/octet-stream")
                return
        if self.apk_path is not None and self.apk_url and request_path == urlparse(self.apk_url).path:
            self._send_file(self.apk_path, "application/vnd.android.package-archive")
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def _send_firmware(self) -> None:
        self._send_file(self.firmware_path, "application/octet-stream")

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            size = path.stat().st_size
            with path.open("rb") as artifact:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while chunk := artifact.read(64 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return
        except OSError as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print("ota %s" % (format % args), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local development OTA update")
    parser.add_argument("--firmware", required=True, type=Path)
    parser.add_argument("--firmware-bean-ye-1", type=Path)
    parser.add_argument("--firmware-bean-ye-2", type=Path)
    parser.add_argument("--apk", type=Path, help="Optional Android APK paired with this release")
    parser.add_argument(
        "--ios-app-url",
        default="",
        help="Optional TestFlight, App Store, or signed iOS distribution URL",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--app-version", help="Optional App version; defaults to firmware version")
    parser.add_argument("--public-base-url", required=True, help="Device-reachable base URL, e.g. http://192.168.0.107:8790")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()

    firmware_path = args.firmware.expanduser().resolve()
    if not firmware_path.is_file():
        parser.error("firmware file does not exist: %s" % firmware_path)
    base_url = args.public_base_url.rstrip("/")
    device_firmwares: dict[str, tuple[Path, str]] = {}
    for device_id, candidate in (("bean_ye_1", args.firmware_bean_ye_1), ("bean_ye_2", args.firmware_bean_ye_2)):
        if candidate is None:
            continue
        path = candidate.expanduser().resolve()
        if not path.is_file():
            parser.error("firmware file does not exist: %s" % path)
        device_firmwares[device_id] = (path, "%s/firmware/%s/%s.bin" % (base_url, args.version, device_id))
    apk_path = args.apk.expanduser().resolve() if args.apk else None
    if apk_path is not None and not apk_path.is_file():
        parser.error("APK file does not exist: %s" % apk_path)
    firmware_url = "%s/firmware/%s.bin" % (base_url, args.version)
    OtaHandler.firmware_path = firmware_path
    OtaHandler.apk_path = apk_path
    OtaHandler.version = args.version
    OtaHandler.app_version = args.app_version or args.version
    OtaHandler.firmware_url = firmware_url
    OtaHandler.device_firmwares = device_firmwares
    OtaHandler.apk_url = "%s/app/%s.apk" % (base_url, OtaHandler.app_version) if apk_path else ""
    OtaHandler.ios_app_url = args.ios_app_url.strip()
    server = ThreadingHTTPServer((args.host, args.port), OtaHandler)
    print("OTA manifest: %s/manifest.json" % base_url, flush=True)
    print("OTA firmware: %s" % firmware_url, flush=True)
    if OtaHandler.apk_url or OtaHandler.ios_app_url:
        print("App release: %s/release.json" % base_url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
