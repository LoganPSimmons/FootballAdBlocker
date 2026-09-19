import unittest

from adblock.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, HotkeyError, parse_hotkey


class ParseHotkey(unittest.TestCase):
    def test_plain_function_key(self):
        self.assertEqual(parse_hotkey("F9"), (0, 0x78))

    def test_modifiers_any_case_and_spacing(self):
        self.assertEqual(parse_hotkey("Ctrl + Shift + f9"), (MOD_CONTROL | MOD_SHIFT, 0x78))
        self.assertEqual(parse_hotkey("alt+m"), (MOD_ALT, ord("M")))

    def test_digits_and_named_keys(self):
        self.assertEqual(parse_hotkey("ctrl+1"), (MOD_CONTROL, 0x31))
        self.assertEqual(parse_hotkey("pause"), (0, 0x13))
        self.assertEqual(parse_hotkey("f24"), (0, 0x87))

    def test_rejects_what_windows_would_reject_or_we_cannot_name(self):
        for bad in ("", "ctrl+", "hyper+f9", "ctrl+banana", "F12"):
            with self.subTest(bad), self.assertRaises(HotkeyError):
                parse_hotkey(bad)

    def test_f12_is_fine_with_a_modifier(self):
        self.assertEqual(parse_hotkey("ctrl+f12"), (MOD_CONTROL, 0x7B))


if __name__ == "__main__":
    unittest.main()
