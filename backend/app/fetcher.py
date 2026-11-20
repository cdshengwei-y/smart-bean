"""录音豆拉取器（2026-11-20）：从设备 TF 卡拉取录音与分段，落本地归档。

架构（用户决策）：设备只管录音，不再自行上传 MinIO；Mac 服务轮询设备
WiFi HTTP 接口拉取数据，转写链路自动接管：

- GET /status            → 设备状态（recording/current_file）
- GET /files             → TF 卡 .wav 清单（含 60s 分段、已完结录音）
- GET /files/<name>      → 下载 WAV

新文件写入本地归档 data/recordings_archive/recordings/<设备>/<文件名>，
scanner 的 InboxArchiveStore 归档发现链路自动处理转写与笔记——
设备零上传负担，出字进度不被设备侧上传拖慢。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from .config import ScannerSettings


class DeviceFetcher:
    """轮询设备 TF 卡，把新录音/分段拉回本地归档。"""

    def __init__(
        self,
        settings: ScannerSettings,
        device_base_url: str = "",
        device_id: str = "bean_ye_2",
        poll_interval_seconds: float = 10.0,
    ) -> None:
        self.settings = settings
        self.device_base_url = (
            device_base_url
            or os.getenv(
                "RECORDER_DEVICE_BASE_URL",
                "http://ai-recorder-bean-ye-2.local",  # 设备 mDNS 主机名（固件 startMdns）
            )
        ).rstrip("/")
        self.device_id = device_id
        self.poll_interval_seconds = poll_interval_seconds
        self.state_path = Path(settings.database_path).parent / "fetcher_state.json"
        self._state: Dict[str, Any] = self._load_state()
        self._lock = threading.Lock()
        self._last_reachable: bool | None = None

    # ---------- 状态持久化 ----------

    def _load_state(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_state(self) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._state, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.state_path)

    # ---------- 归档路径 ----------

    def _archive_path(self, name: str) -> Path:
        """归档路径：分段（含 __s 标记）必须保留 live/<stem>/ 子结构，
        与设备 MinIO key 同构——否则会被常规发现链路当成独立录音，
        各分段各出一篇笔记（2026-11-20 实测教训）。"""
        archive_root = Path(self.settings.database_path).parent / "recordings_archive"
        device_dir = archive_root / self.settings.recordings_prefix.rstrip("/") / self.device_id
        if "__s" in name:
            stem = name.split("__s")[0]
            segment_dir = device_dir / "live" / stem
            segment_dir.mkdir(parents=True, exist_ok=True)
            return segment_dir / name
        device_dir.mkdir(parents=True, exist_ok=True)
        return device_dir / name

    # ---------- 设备交互 ----------

    def _request(self, path: str, timeout: float = 8.0) -> tuple[int, Any]:
        parsed = urlsplit(self.device_base_url)
        host = parsed.hostname
        if not host:
            raise RuntimeError("未配置设备地址（RECORDER_DEVICE_BASE_URL）")
        port = parsed.port or 80
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            connection.request("GET", path, headers={"Accept": "*/*"})
            response = connection.getresponse()
            body = response.read()
            return response.status, body
        finally:
            connection.close()

    def list_device_files(self) -> list[dict]:
        status, body = self._request("/files")
        if status != 200:
            raise RuntimeError("设备 /files 返回 %d" % status)
        document = json.loads(body.decode("utf-8"))
        files = document.get("files") or []
        return [item for item in files if str(item.get("name", "")).endswith(".wav")]

    def download_file(self, name: str, destination: Path) -> None:
        from urllib.parse import quote

        status, body = self._request("/files/" + quote(name), timeout=30.0)
        if status != 200:
            raise RuntimeError("设备下载 %s 返回 %d" % (name, status))
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".downloading")
        temporary.write_bytes(body)
        temporary.replace(destination)

    # ---------- 主循环 ----------

    def poll_once(self) -> int:
        """拉取一轮新文件，返回拉取数量。"""
        if not self.device_base_url:
            return 0
        try:
            files = self.list_device_files()
        except Exception as exc:
            if self._last_reachable is not False:
                print(
                    "[%s] [FETCHER] 设备不可达 %s: %s（恢复后自动继续）" % (
                        time.strftime("%H:%M:%S"), self.device_base_url, exc
                    ),
                    flush=True,
                )
            self._last_reachable = False
            return 0
        if self._last_reachable is False:
            print(
                "[%s] [FETCHER] 设备已恢复可达 %s" % (
                    time.strftime("%H:%M:%S"), self.device_base_url
                ),
                flush=True,
            )
        self._last_reachable = True
        fetched = 0
        with self._lock:
            for item in files:
                name = str(item.get("name") or "").strip()
                size = int(item.get("size") or 0)
                if not name or size <= 44:  # 只有 WAV 头的空文件跳过
                    continue
                known = self._state.get(name)
                if known and known.get("size") == size and known.get("fetched"):
                    continue  # 已拉取且大小未变（录音中的文件会随写卡增长）
                destination = self._archive_path(name)
                try:
                    self.download_file(name, destination)
                except Exception as exc:
                    print(
                        "[%s] [FETCHER] 下载失败 %s: %s" % (
                            time.strftime("%H:%M:%S"), name, exc
                        ),
                        flush=True,
                    )
                    continue
                if destination.stat().st_size != size:
                    # 录音进行中的 .part 内容在增长——不落状态，下轮再拉
                    continue
                self._state[name] = {"size": size, "fetched": True}
                fetched += 1
                print(
                    "[%s] [FETCHER] 拉取 %s (%d bytes)" % (
                        time.strftime("%H:%M:%S"), name, size
                    ),
                    flush=True,
                )
            if fetched:
                self._save_state()
        return fetched

    def run_forever(self) -> None:
        print(
            "[FETCHER] 启动 device=%s interval=%.0fs" % (
                self.device_base_url or "(未配置)", self.poll_interval_seconds
            ),
            flush=True,
        )
        while True:
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - 轮询永不退出
                print(
                    "[%s] [FETCHER] 轮询异常: %s: %s" % (
                        time.strftime("%H:%M:%S"), type(exc).__name__, exc
                    ),
                    flush=True,
                )
            time.sleep(self.poll_interval_seconds)
