"""The docs must name every setting and every command-line flag the code has - the cheap way to keep them honest."""

import re
import unittest
from dataclasses import fields
from pathlib import Path

from adblock import config

ROOT = Path(__file__).resolve().parents[1]


class SettingsAreDocumented(unittest.TestCase):
    def setUp(self):
        self.doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")

    def test_every_setting_is_in_the_configuration_doc_and_the_example_file(self):
        example = (ROOT / "config.example.toml").read_text(encoding="utf-8")
        for field in fields(config.Config):
            self.assertIn(f"`{field.name}`", self.doc, f"{field.name} is missing from docs/configuration.md")
            self.assertRegex(example, rf"(?m)^#?\s*{field.name}\s*=", f"{field.name} is missing from config.example.toml")

    def test_every_command_line_flag_is_in_the_configuration_doc(self):
        from adblock.app import build_parser

        for action in build_parser()._actions:
            for flag in action.option_strings:
                if flag.startswith("--") and flag != "--help":
                    self.assertIn(f"`{flag}`", self.doc, f"{flag} is missing from docs/configuration.md")

    def test_the_readme_points_at_every_doc(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for doc in sorted((ROOT / "docs").glob("*.md")):
            self.assertIn(f"docs/{doc.name}", readme, f"README.md does not link docs/{doc.name}")
        for link in re.findall(r"\]\(((?:docs/|tools/|tests/)[^)#]+)\)", readme):
            self.assertTrue((ROOT / link).exists(), f"README.md links to a missing file: {link}")


if __name__ == "__main__":
    unittest.main()
