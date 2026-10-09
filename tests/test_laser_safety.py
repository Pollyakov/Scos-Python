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

The same module holds the end-of-measurement windows (todo U2), tested at the
bottom: the red "laser may still be on" window and the probe-removal window
shown once the session is saved. When MainWindow opens which one is tested in
tests/test_laser_off_check.py.
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

from gui import safety_dialog  # noqa: E402
from gui.safety_dialog import (  # noqa: E402
    CONFIRM_TEXT, EXIT_TEXT, LaserSafetyDialog, confirm_laser_safety,
    LaserStillOnDialog, ProbeRemovalDialog, ask_continue_with_laser_on,
    show_probe_removal, PROBE_PASSED, PROBE_SKIPPED, PROBE_FAILED, PROBE_CANCELLED,
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


# ---------------------------------------------------------------------------
# U2 — the red "laser may still be on" window
# ---------------------------------------------------------------------------

class TestLaserStillOn:
    def test_title_and_numbers(self):
        d = LaserStillOnDialog(114.5, 11.4)
        text = d.text_label.text()
        assert d.windowTitle() == "Laser May Still Be On"
        assert "LASER MAY STILL BE ON" in text
        assert "did not drop by 90" in text
        assert "114.5 DU" in text, "the measured value is quoted"
        assert "11.4 DU" in text, "and the expected ceiling"

    def test_it_is_red(self):
        style = LaserStillOnDialog(114.5, 11.4).styleSheet()
        assert "background-color: #7a1010" in style
        assert "border: 6px solid #ff4d4d" in style

    def test_continue_anyway_is_the_only_way_out(self, monkeypatch):
        _answer_with(monkeypatch, lambda d: d.continue_button.click())
        assert ask_continue_with_laser_on(None, 114.5, 11.4) is True

    @pytest.mark.parametrize("answer", [
        lambda d: d.check_again_button.click(),
        lambda d: QTest.keyClick(d, Qt.Key.Key_Escape),
        lambda d: d.close(),
        lambda d: None,
    ], ids=["check-again", "escape", "title-bar-x", "no-answer"])
    def test_everything_else_means_check_again(self, monkeypatch, answer):
        _answer_with(monkeypatch, answer)
        assert ask_continue_with_laser_on(None, 114.5, 11.4) is False

    def test_enter_means_check_again(self):
        d = LaserStillOnDialog(114.5, 11.4)
        assert d.check_again_button.isDefault()
        assert not d.continue_button.isDefault()
        assert not d.continue_button.autoDefault()


# ---------------------------------------------------------------------------
# After the save — the probe-removal window
# ---------------------------------------------------------------------------

class TestProbeRemoval:
    @pytest.mark.parametrize("outcome", [PROBE_PASSED, PROBE_SKIPPED, PROBE_FAILED,
                                         PROBE_CANCELLED])
    def test_every_outcome_repeats_the_safety_rules(self, outcome):
        """Even after a passed check: the camera going dark is not the rule,
        the laser's red indicator light is (U1)."""
        d = ProbeRemovalDialog(outcome)
        text = d.text_label.text().lower()
        assert "red indicator light" in text
        assert "rubber strap backwards" in text

    @pytest.mark.parametrize("outcome", [PROBE_PASSED, PROBE_SKIPPED, PROBE_FAILED])
    def test_after_a_measurement_it_says_the_data_is_saved(self, outcome):
        assert "data is saved" in ProbeRemovalDialog(outcome).text_label.text()

    def test_cancelled_calibration_says_switch_the_laser_off(self):
        d = ProbeRemovalDialog(PROBE_CANCELLED)
        text = d.text_label.text()
        assert "Calibration stopped" in text
        assert "Switch the laser off" in text
        assert "data is saved" not in text, "nothing was measured"
        assert "#7a1010" in d.styleSheet(), "red window"

    def test_passed_says_the_probe_may_come_off(self):
        d = ProbeRemovalDialog(PROBE_PASSED)
        assert "you may remove the probe" in d.text_label.text()
        assert "#2e9e4f" in d.styleSheet(), "green frame"

    def test_skipped_says_check_yourself(self):
        d = ProbeRemovalDialog(PROBE_SKIPPED)
        assert "could not run" in d.text_label.text()
        assert "Check yourself" in d.text_label.text()
        assert "#e0a020" in d.styleSheet(), "amber frame"

    def test_failed_says_do_not_remove_yet(self):
        d = ProbeRemovalDialog(PROBE_FAILED)
        assert "do NOT remove the probe yet" in d.text_label.text()
        assert "#7a1010" in d.styleSheet(), "red window"

    def test_titles_differ(self):
        titles = {ProbeRemovalDialog(o).windowTitle()
                  for o in (PROBE_PASSED, PROBE_SKIPPED, PROBE_FAILED, PROBE_CANCELLED)}
        assert len(titles) == 4

    def test_ok_is_the_default(self):
        d = ProbeRemovalDialog(PROBE_PASSED)
        assert d.ok_button.isDefault()

    def test_show_logs_the_outcome(self, monkeypatch, caplog):
        _answer_with(monkeypatch, lambda d: d.ok_button.click())
        with caplog.at_level(logging.INFO, logger="gui.safety_dialog"):
            show_probe_removal(None, PROBE_SKIPPED)
        assert "Probe-removal instructions shown — laser-off check skipped" in caplog.text


@pytest.mark.parametrize("make", [
    lambda: LaserSafetyDialog(),
    lambda: LaserStillOnDialog(114.5, 11.4),
    lambda: ProbeRemovalDialog(PROBE_PASSED),
    lambda: ProbeRemovalDialog(PROBE_SKIPPED),
    lambda: ProbeRemovalDialog(PROBE_FAILED),
    lambda: ProbeRemovalDialog(PROBE_CANCELLED),
], ids=["startup", "still-on", "probe-passed", "probe-skipped", "probe-failed",
        "probe-cancelled"])
def test_every_window_is_roomy_and_fits_the_rig_screen(make):
    d = make()
    assert 900 <= d.width() <= 1280
    assert d.sizeHint().height() <= 752
    m = d.layout().contentsMargins()
    assert min(m.left(), m.top(), m.right(), m.bottom()) >= 30
