from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.content_filter.profile import WorkProfileStore


class WorkProfileStoreTests(unittest.TestCase):
    def test_old_profile_defaults_to_work_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "work_profiles.json"
            path.write_text('{"__default__":{"enabled":true,"work_types":["software_development"]}}', encoding="utf-8")
            profile = WorkProfileStore(path).get("__default__")
        self.assertEqual(profile["recording_scene"], "work")

    def test_saves_supported_recording_scene(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkProfileStore(Path(directory) / "work_profiles.json")
            profile = store.put("__default__", {"enabled": True, "recording_scene": "meeting"})
        self.assertEqual(profile["recording_scene"], "meeting")

    def test_invalid_recording_scene_falls_back_to_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkProfileStore(Path(directory) / "work_profiles.json")
            profile = store.put("__default__", {"recording_scene": "invalid"})
        self.assertEqual(profile["recording_scene"], "work")


if __name__ == "__main__":
    unittest.main()
