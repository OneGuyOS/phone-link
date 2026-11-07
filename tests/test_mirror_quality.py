"""Headless UI tests for the mirror quality dialog."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from phone_link.scrcpy import MirrorQuality
from phone_link.ui.dialogs import MirrorQualityDialog
from phone_link.ui.mirror import MirrorPanel


class MirrorQualityDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, quality: MirrorQuality) -> MirrorQualityDialog:
        dialog = MirrorQualityDialog(quality)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_shows_the_current_settings(self) -> None:
        quality = MirrorQuality(
            bit_rate=16_000_000,
            max_size=720,
            max_fps=30,
            turn_screen_off=True,
        )
        self.assertEqual(self._dialog(quality).quality(), quality)

    def test_defaults_round_trip(self) -> None:
        self.assertEqual(self._dialog(MirrorQuality()).quality(), MirrorQuality())

    def test_unknown_stored_value_falls_back_to_the_default(self) -> None:
        dialog = self._dialog(MirrorQuality(bit_rate=12_345))
        self.assertEqual(dialog.bit_rate.currentData(), 0)

    def test_choosing_options_builds_new_settings(self) -> None:
        dialog = self._dialog(MirrorQuality())
        dialog.bit_rate.setCurrentIndex(dialog.bit_rate.findData(4_000_000))
        dialog.size_limit.setCurrentIndex(dialog.size_limit.findData(540))
        dialog.frame_rate.setCurrentIndex(dialog.frame_rate.findData(60))
        dialog.screen_off.setChecked(True)
        self.assertEqual(
            dialog.quality(),
            MirrorQuality(
                bit_rate=4_000_000,
                max_size=540,
                max_fps=60,
                turn_screen_off=True,
            ),
        )


class MirrorPanelQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_panel_has_a_quality_button(self) -> None:
        panel = MirrorPanel(None, None)
        self.addCleanup(panel.deleteLater)
        self.assertTrue(panel.quality_button.toolTip())
        self.assertIsInstance(panel.quality, MirrorQuality)
