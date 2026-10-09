"""
Laser-safety warning at startup (todo U1).

With the real camera, `main.py` shows a warning the operator must confirm
before anything else opens: goggles for the whole measurement, the probe off
only after the laser's red indicator light is out, and only by pulling the
rubber strap backwards. Anything but "I confirm" — Exit, Escape, the title-bar
X — closes the app. The playback modes don't show it (user's decision,
2026-10-09: no laser, no subject).

`main.py` itself is NOT imported here: at import it opens `app.log` with
mode="w", which would wipe the developer's log on every test run. Its wiring
is checked by reading the source instead.
"""

import ast
import logging
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from gui.safety_dialog import (  # noqa: E402
    CONFIRM_TEXT, EXIT_TEXT, LaserSafetyDialog, confirm_laser_safety,
)

_MAIN = Path(__file__).resolve().parent.parent / "main.py"


def _answer_with(monkeypatch, action):
    """Replace QDialog.exec (blocked by conftest) with `action(dialog)`."""
    def _exec(dialog):
        action(dialog)
        return dialog.result()
    monkeypatch.setattr(QDialog, "exec", _exec)


class TestWording:
    def test_all_three_instructions_are_shown(self):
        d = LaserSafetyDialog()
        text = d.text_label.text().lower()
        assert "laser-safety goggles" in text
        assert "whole measurement" in text
        assert "red indicator light" in text
        assert "not lit" in text
        assert "rubber strap backwards" in text

    def test_title_and_buttons(self):
        d = LaserSafetyDialog()
        assert d.windowTitle() == "Laser safety"
        assert d.confirm_button.text() == CONFIRM_TEXT
        assert d.exit_button.text() == EXIT_TEXT

    def test_roomy_and_fits_the_rig_screen(self):
        """The user asked for a larger window with real margins (2026-10-09);
        the rig PC's screen is 1280 × 752."""
        d = LaserSafetyDialog()
        assert 900 <= d.width() <= 1280
        assert d.sizeHint().height() <= 752
        m = d.layout().contentsMargins()
        assert min(m.left(), m.top(), m.right(), m.bottom()) >= 30


class TestAnswer:
    def test_confirm_continues(self, monkeypatch, caplog):
        _answer_with(monkeypatch, lambda d: d.confirm_button.click())
        with caplog.at_level(logging.INFO, logger="gui.safety_dialog"):
            assert confirm_laser_safety() is True
        assert "Laser-safety warning confirmed" in caplog.text

    def test_exit_stops(self, monkeypatch, caplog):
        _answer_with(monkeypatch, lambda d: d.exit_button.click())
        with caplog.at_level(logging.INFO, logger="gui.safety_dialog"):
            assert confirm_laser_safety() is False
        assert "not confirmed" in caplog.text

    def test_title_bar_close_is_not_consent(self, monkeypatch):
        _answer_with(monkeypatch, lambda d: d.close())
        assert confirm_laser_safety() is False

    def test_escape_key_is_not_consent(self, monkeypatch):
        _answer_with(monkeypatch, lambda d: QTest.keyClick(d, Qt.Key.Key_Escape))
        assert confirm_laser_safety() is False

    def test_no_answer_is_not_consent(self, monkeypatch):
        _answer_with(monkeypatch, lambda d: None)
        assert confirm_laser_safety() is False

    def test_enter_key_means_exit(self):
        """Enter must not confirm by accident (user's decision, 2026-10-09):
        Exit is the default button and has the focus; "I confirm" never
        becomes the default by itself."""
        d = LaserSafetyDialog()
        assert d.exit_button.isDefault()
        assert not d.confirm_button.isDefault()
        assert not d.confirm_button.autoDefault()


def _mode_branches():
    """main()'s if/elif/else over the run modes → {first log text: body}."""
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    main_fn = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    node = next(n for n in main_fn.body
                if isinstance(n, ast.If) and "mock_h5" in ast.unparse(n.test))
    branches = {}
    while True:
        branches[ast.unparse(node.body[0])] = node.body
        if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
            node = node.orelse[0]
        else:
            branches[ast.unparse(node.orelse[0])] = node.orelse
            return branches


class TestWiring:
    def test_real_camera_asks_before_opening_the_camera(self):
        real = next(body for first, body in _mode_branches().items()
                    if "real Basler camera" in first)
        src = "\n".join(ast.unparse(s) for s in real)
        assert "if not confirm_laser_safety():" in src
        assert "sys.exit(0)" in src
        assert src.index("confirm_laser_safety()") < src.index("CameraThread()")
        assert src.index("confirm_laser_safety()") < src.index("MainWindow(")

    def test_playback_modes_do_not_ask(self):
        branches = _mode_branches()
        assert len(branches) == 4
        for first, body in branches.items():
            if "real Basler camera" in first:
                continue
            src = "\n".join(ast.unparse(s) for s in body)
            assert "confirm_laser_safety" not in src, first
