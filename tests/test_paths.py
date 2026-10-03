"""Where the app writes: next to the code from a clone, under %LOCALAPPDATA% when installed."""

import tempfile
import unittest
from pathlib import Path

from adblock import paths


class DataFolder(unittest.TestCase):
    def test_a_clone_keeps_its_data_in_the_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(paths.data_dir(Path(tmp), {}), Path(tmp))

    def test_an_installed_copy_uses_local_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / paths.MARKER).write_text("installed", encoding="utf-8")
            self.assertEqual(paths.data_dir(root, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}),
                             Path(r"C:\Users\x\AppData\Local\FootballAdBlocker"))

    def test_the_environment_variable_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / paths.MARKER).write_text("installed", encoding="utf-8")
            self.assertEqual(paths.data_dir(root, {paths.ENV: r"D:\games\adblock"}), Path(r"D:\games\adblock"))


if __name__ == "__main__":
    unittest.main()
