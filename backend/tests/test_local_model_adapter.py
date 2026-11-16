from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from app.transcription.contract import TranscriptionRequest
from app.transcription.local_model_adapter import LocalModelTranscriber


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class ChunkingSession:
    def __init__(self) -> None:
        self.filenames = []

    def post(self, _url, files, timeout):
        filename = files["file"][0]
        self.filenames.append(filename)
        if filename == "chunk_000.wav":
            return FakeResponse({"text": "第一段文字", "language": "zh"})
        if filename == "chunk_001.wav":
            return FakeResponse({"text": "第二段文字", "language": "zh"})
        return FakeResponse({"text": ""})


class EmptyTextSession:
    def post(self, _url, files, timeout):
        return FakeResponse({"text": "", "language": "zh", "provider": "fake-asr"})


class LocalModelAdapterTest(unittest.TestCase):
    def test_transcribes_long_recordings_in_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "long.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(10)
                audio.writeframes(b"\x00\x00" * 25)

            session = ChunkingSession()
            transcriber = LocalModelTranscriber(
                base_url="http://asr.test",
                chunk_seconds=1,
                session=session,
            )
            result = transcriber.transcribe(TranscriptionRequest(audio_path=audio_path, language="zh"))

        self.assertEqual(result.text, "第一段文字\n\n第二段文字")
        self.assertEqual(result.metadata["chunked"], "true")
        self.assertEqual(
            sorted(session.filenames),
            ["chunk_000.wav", "chunk_001.wav", "chunk_002.wav"],
        )

    def test_chunk_progress_reports_ordered_prefix_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "long.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(10)
                audio.writeframes(b"\x00\x00" * 25)

            session = ChunkingSession()
            transcriber = LocalModelTranscriber(
                base_url="http://asr.test",
                chunk_seconds=1,
                session=session,
            )
            progress = []
            transcriber.transcribe_with_progress(
                TranscriptionRequest(audio_path=audio_path, language="zh"),
                on_progress=progress.append,
            )

        self.assertTrue(progress)
        self.assertEqual(progress[0].text, "第一段文字")
        self.assertIn("第二段文字", progress[-1].text)
        self.assertEqual(progress[-1].metadata["completed_chunks"], "3")
        self.assertEqual(progress[-1].metadata["total_chunks"], "3")

    def test_empty_asr_result_is_a_completed_empty_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "empty.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(b"\x00\x00" * 160)

            result = LocalModelTranscriber(
                base_url="http://asr.test",
                session=EmptyTextSession(),
            ).transcribe(TranscriptionRequest(audio_path=audio_path, language="zh"))

        self.assertEqual(result.text, "")
        self.assertEqual(result.metadata["empty_transcript"], "true")

if __name__ == "__main__":
    unittest.main()
