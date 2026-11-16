from pathlib import Path
import json
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from flash_release import verify_release_policy


class VerifyReleasePolicyTest(unittest.TestCase):
    def write_manifest(self, release: Path, **updates: str) -> None:
        manifest = {
            "self_test_environment": "xiao_esp32s3_custom_expansion_self_test",
            "production_environment": "xiao_esp32s3_custom_board_release_038",
            "force_sleep_environment": "xiao_esp32s3_custom_board_force_sleep_test_038",
            "expected_device_id": "bean_ye_2",
        }
        manifest.update(updates)
        (release / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_accepts_current_production_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            self.write_manifest(release)
            verify_release_policy(release, "production")

    def test_rejects_old_production_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            self.write_manifest(release, production_environment="xiao_esp32s3_new_board_release_037")
            with self.assertRaises(SystemExit):
                verify_release_policy(release, "production")

    def test_rejects_wrong_expected_device_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory)
            self.write_manifest(release, expected_device_id="bean-F865DF90A994")
            with self.assertRaises(SystemExit):
                verify_release_policy(release, "force-sleep")


if __name__ == "__main__":
    unittest.main()
