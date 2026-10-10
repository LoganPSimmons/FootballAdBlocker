"""The title rule behind the control window's Chrome window picker (pure functions in win32.py)."""

import unittest

from adblock.win32 import chrome_page_title, stream_titles, title_matches, title_needles


class ChromePageTitle(unittest.TestCase):
    def test_chrome_suffix_and_notification_count_go(self):
        self.assertEqual(chrome_page_title("Home - YouTube TV - Google Chrome"), "Home - YouTube TV")
        self.assertEqual(chrome_page_title("(2) Inbox - Gmail - Google Chrome"), "Inbox - Gmail")
        self.assertEqual(chrome_page_title("Fubo - Watch & DVR Live Sports & TV Online - Google Chrome"),
                         "Fubo - Watch & DVR Live Sports & TV Online")

    def test_a_title_without_the_suffix_is_kept(self):
        self.assertEqual(chrome_page_title("Google Chrome"), "Google Chrome")
        self.assertEqual(chrome_page_title("Something else"), "Something else")


class TitleTexts(unittest.TestCase):
    def test_the_setting_is_split_on_bars(self):
        self.assertEqual(title_needles(" YouTube TV | Fubo "), ["youtube tv", "fubo"])
        self.assertEqual(title_needles(""), [])
        self.assertEqual(title_needles(None), [])

    def test_a_list_of_texts_is_taken_as_is(self):  # a page title can contain "|" itself
        self.assertTrue(title_matches("Utah | Sling TV - Google Chrome", ("youtube tv", "utah | sling tv")))
        self.assertFalse(title_matches("Utah | Sling TV - Google Chrome", ("youtube tv", "fubo")))
        self.assertFalse(title_matches("Home - YouTube TV - Google Chrome", ()))


class StreamTitles(unittest.TestCase):
    def test_automatic_is_just_the_setting(self):
        self.assertEqual(stream_titles("YouTube TV|Fubo"), ("youtube tv", "fubo"))

    def test_a_picked_window_a_service_names_adds_nothing(self):
        self.assertEqual(stream_titles("YouTube TV|Fubo", "Home - YouTube TV - Google Chrome"), ("youtube tv", "fubo"))

    def test_a_picked_window_of_an_unknown_service_is_known_by_its_page_title(self):
        titles = stream_titles("YouTube TV|Fubo", "Utah | Sling TV - Google Chrome")
        self.assertEqual(titles, ("youtube tv", "fubo", "utah | sling tv"))
        self.assertTrue(title_matches("Utah | Sling TV - Google Chrome", titles))
        self.assertTrue(title_matches("Home - YouTube TV - Google Chrome", titles))
        self.assertFalse(title_matches("Inbox - Gmail - Google Chrome", titles))  # another tab in that window


if __name__ == "__main__":
    unittest.main()
