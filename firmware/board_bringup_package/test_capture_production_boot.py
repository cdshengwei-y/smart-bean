from __future__ import annotations

from pathlib import Path
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_production_boot import inspect_capture


PACKAGE_DIR = Path(__file__).resolve().parent


class ProductionBootCaptureTest(unittest.TestCase):
    def test_rejects_valid_but_unexpected_device_id(self) -> None:
        captured = (PACKAGE_DIR / "logs" / "20260810-171636_1_production_boot.log").read_text(
            encoding="utf-8"
        )

        result = inspect_capture(captured, "bean_ye_2")

        self.assertTrue(result["identity_format_ok"])
        self.assertFalse(result["identity_ok"])
        self.assertEqual(result["device_id"], "bean-F865DF90A994")
        self.assertEqual(result["overall"], "FAIL")

    def test_accepts_expected_device_with_new_board_peripherals(self) -> None:
        captured = "\n".join(
            (
                "Firmware: 0.3.8, device ID: bean_ye_2",
                "microphone PDM channel: right",
                "microSD: ready",
                "microphone: ready",
                "BLE control ready.",
            )
        )

        result = inspect_capture(captured, "bean_ye_2")

        self.assertTrue(result["identity_ok"])
        self.assertEqual(result["overall"], "PASS")


if __name__ == "__main__":
    unittest.main()
