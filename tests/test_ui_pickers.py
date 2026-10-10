"""The control window's pickers without a screen: the searchable game list and the self-refreshing window list."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no window may ever appear: the real app may be running beside the tests

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from adblock.ui import SEARCH_ROLE, RefreshingComboBox, SearchableComboBox  # noqa: E402

Qt = QtCore.Qt
GAMES = [("USU @ UTAH", "Utah State Aggies at Utah Utes", "FOX", "LIVE"),
         ("OSU @ MICH", "Ohio State Buckeyes at Michigan Wolverines", "CBS", "later"),
         ("UTSA @ TEX", "UTSA Roadrunners at Texas Longhorns", "ESPN", "final")]


def application() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def key(code: Qt.Key, text: str = "") -> QtGui.QKeyEvent:
    return QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, code, Qt.KeyboardModifier.NoModifier, text)


class SearchableGameList(unittest.TestCase):
    def setUp(self):
        application()
        self.box = SearchableComboBox()
        self.box.addItem("none - the ad screen shows no score", ("", ""))
        for short, name, network, tag in GAMES:
            self.box.addItem(f"{short}   {network}   {tag}", (short, short))
            self.box.setItemData(self.box.count() - 1, name, SEARCH_ROLE)
        self.picked: list[int] = []
        self.box.activated.connect(self.picked.append)

    def tearDown(self):
        self.box.hidePopup()

    def rows(self) -> list[str]:
        f = self.box.filter
        return [f.data(f.index(i, 0)) for i in range(f.rowCount())]

    def test_every_word_must_occur_in_the_entry_or_the_full_name(self):
        self.box.narrow("utah")
        self.assertEqual(self.rows(), ["USU @ UTAH   FOX   LIVE"])
        self.box.narrow("Aggies")  # the full name, any case
        self.assertEqual(self.rows(), ["USU @ UTAH   FOX   LIVE"])
        self.box.narrow("live fox")  # several words, any order
        self.assertEqual(self.rows(), ["USU @ UTAH   FOX   LIVE"])
        self.box.narrow("michigan")
        self.assertEqual(self.rows(), ["OSU @ MICH   CBS   later"])
        self.box.narrow("zzz")
        self.assertEqual(self.rows(), [])
        self.box.narrow("")
        self.assertEqual(len(self.rows()), 4)

    def test_a_click_picks_the_entry_behind_the_filter(self):
        self.box.narrow("mich")
        self.box.pick(self.box.filter.index(0, 0))
        self.assertEqual(self.box.currentIndex(), 2)
        self.assertEqual(self.picked, [2])
        self.assertEqual(self.box.itemData(self.box.currentIndex()), ("OSU @ MICH", "OSU @ MICH"))

    def test_enter_picks_the_first_match_and_the_arrows_move(self):
        self.box.narrow("tex")
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Return))
        self.assertEqual(self.picked, [3])
        self.box.narrow("")  # the current pick is highlighted; Up/Down move from there
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Up))
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Up))
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Return))
        self.assertEqual(self.picked, [3, 1])

    def test_enter_on_no_match_picks_nothing(self):
        self.box.narrow("zzz")
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Return))
        self.assertEqual(self.picked, [])
        self.assertEqual(self.box.currentIndex(), 0)

    def test_typing_into_the_closed_box_opens_the_list_with_that_letter(self):
        QtWidgets.QApplication.sendEvent(self.box, key(Qt.Key.Key_M, "m"))
        self.assertTrue(self.box.popup.isVisible())
        self.assertEqual(self.box.search.text(), "m")
        self.assertEqual(self.rows(), ["OSU @ MICH   CBS   later"])
        QtWidgets.QApplication.sendEvent(self.box.search, key(Qt.Key.Key_Escape))
        self.assertFalse(self.box.popup.isVisible())

    def test_the_list_opens_cleared_and_closes_on_a_pick(self):
        self.box.narrow("utah")
        self.box.showPopup()
        self.assertTrue(self.box.popup.isVisible())
        self.assertEqual(self.box.search.text(), "")
        self.assertEqual(len(self.rows()), 4)
        self.box.pick(self.box.filter.index(1, 0))
        self.assertFalse(self.box.popup.isVisible())
        self.assertEqual(self.picked, [1])

    def test_the_wheel_does_not_change_the_pick(self):
        wheel = QtGui.QWheelEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                                  Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(self.box, wheel)
        self.assertEqual(self.box.currentIndex(), 0)
        self.assertEqual(self.picked, [])


class RefreshingWindowList(unittest.TestCase):
    def test_the_entries_are_rebuilt_whenever_the_list_opens(self):
        application()
        opened: list[int] = []
        box = RefreshingComboBox(lambda: opened.append(box.count()))
        box.addItem("Automatic", 0)
        box.addItem("Home - YouTube TV   ·   fullscreen", 0x1234)
        box.showPopup()
        box.hidePopup()
        box.showPopup()
        box.hidePopup()
        self.assertEqual(opened, [2, 2])
        self.assertEqual(box.findData(0x1234), 1)
        self.assertEqual(box.findData(0x9999), -1)


if __name__ == "__main__":
    unittest.main()
