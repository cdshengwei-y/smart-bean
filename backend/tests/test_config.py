from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import _obsidian_vault_path


class ObsidianVaultConfigTests(unittest.TestCase):
    def test_direct_vault_sync_is_disabled_without_explicit_path(self) -> None:
        with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": ""}, clear=False):
            self.assertIsNone(_obsidian_vault_path())

    def test_explicit_vault_path_enables_direct_vault_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            with patch.dict(os.environ, {"OBSIDIAN_VAULT_PATH": str(vault)}, clear=False):
                self.assertEqual(_obsidian_vault_path(), vault)


if __name__ == "__main__":
    unittest.main()
