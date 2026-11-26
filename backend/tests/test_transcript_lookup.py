from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.database import JobRepository
from app.storage.contract import RecordingObject


def make_repository(root: Path) -> JobRepository:
    return JobRepository(root / "jobs.db")


def discover_recording(
    repository: JobRepository,
    object_name: str,
    device_id: str = "bean_x",
) -> int:
    repository.discover(
        [
            RecordingObject(
                object_name=object_name,
                etag="e-" + object_name,
                size=10,
                last_modified=datetime.now(timezone.utc),
            )
        ]
    )
    return int(
        repository.connection.execute(
            "SELECT id FROM transcription_jobs WHERE object_name = ?",
            (object_name,),
        ).fetchone()["id"]
    )


def save_transcript(repository: JobRepository, job_id: int, transcript: str) -> None:
    repository.save_transcript(
        job_id,
        transcript=transcript,
        note_object_name="notes/placeholder.md",
        language="zh",
        transcription_model="fake-asr",
        duration_seconds=1.0,
        download_seconds=0.1,
        asr_seconds=0.1,
    )


class LatestTranscriptForTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repository = make_repository(Path(self._tmp.name))

    def tearDown(self) -> None:
        self.repository.close()
        self._tmp.cleanup()

    def test_finds_recording_by_stem(self) -> None:
        job_id = discover_recording(
            self.repository, "recordings/bean_x/REC_20261126_160153_0.wav"
        )
        save_transcript(self.repository, job_id, "第一句\n第二句")
        row = self.repository.latest_transcript_for("REC_20261126_160153_0")
        self.assertIsNotNone(row)
        self.assertIn("第一句", str(row["transcript"]))

    def test_underscore_is_escaped_not_a_wildcard(self) -> None:
        job_id = discover_recording(
            self.repository, "recordings/bean_x/REC_A_B.wav"
        )
        save_transcript(self.repository, job_id, "内容")
        # REC_AXB 不应命中 REC_A_B（下划线不是通配符）
        self.assertIsNone(self.repository.latest_transcript_for("REC_AXB"))
        # REC_AB 也不应命中
        self.assertIsNone(self.repository.latest_transcript_for("REC_AB"))
        row = self.repository.latest_transcript_for("REC_A_B")
        self.assertIsNotNone(row)

    def test_authoritative_row_beats_live_preview_row(self) -> None:
        live_job = discover_recording(
            self.repository,
            "recordings/bean_x/live/REC_001/REC_001__agg.wav",
            device_id="bean_x",
        )
        save_transcript(self.repository, live_job, "直播预览文本")
        full_job = discover_recording(
            self.repository, "recordings/bean_x/REC_001.wav", device_id="bean_x"
        )
        save_transcript(self.repository, full_job, "权威完整文本")
        row = self.repository.latest_transcript_for("REC_001")
        self.assertIsNotNone(row)
        self.assertEqual(str(row["transcript"]), "权威完整文本")

    def test_device_id_preference_does_not_exclude_others(self) -> None:
        other_job = discover_recording(
            self.repository,
            "recordings/legacy_id/REC_002.wav",
            device_id="legacy_id",
        )
        save_transcript(self.repository, other_job, "旧设备文本")
        # 查询方传的 device_id 与库里的不一致时，仍应能命中（不硬绑）
        row = self.repository.latest_transcript_for("REC_002", device_id="bean_ye_2")
        self.assertIsNotNone(row)
        self.assertEqual(str(row["transcript"]), "旧设备文本")

    def test_empty_transcript_is_ignored(self) -> None:
        discover_recording(self.repository, "recordings/bean_x/REC_003.wav")
        # 没写过 transcript（默认 ''）→ 不可命中
        self.assertIsNone(self.repository.latest_transcript_for("REC_003"))

    def test_accepts_wav_suffix_input(self) -> None:
        job_id = discover_recording(self.repository, "recordings/bean_x/REC_004.wav")
        save_transcript(self.repository, job_id, "文本")
        # App 传整名 REC_004.wav 时由 server 侧剥掉 .wav；这里验证方法本身
        # 不带 .wav 的路径，剥后缀逻辑属于 handler 职责。
        row = self.repository.latest_transcript_for("REC_004")
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
