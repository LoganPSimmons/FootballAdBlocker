import unittest

from adblock.win32 import title_matches


class StreamWindowTitle(unittest.TestCase):
    def test_any_listed_service_matches(self):
        setting = "YouTube TV|Fubo"
        self.assertTrue(title_matches("Home - YouTube TV - Google Chrome", setting))
        self.assertTrue(title_matches("Fubo - Watch & DVR Live Sports & TV Online - Google Chrome", setting))  # live 2026-09-26
        self.assertFalse(title_matches("utah football - Google Search - Google Chrome", setting))

    def test_case_and_spacing_do_not_matter(self):
        self.assertTrue(title_matches("FUBO - Google Chrome", " youtube tv | fubo "))

    def test_empty_setting_matches_nothing(self):
        self.assertFalse(title_matches("Home - YouTube TV - Google Chrome", ""))
        self.assertFalse(title_matches("Home - YouTube TV - Google Chrome", "|"))


if __name__ == "__main__":
    unittest.main()
