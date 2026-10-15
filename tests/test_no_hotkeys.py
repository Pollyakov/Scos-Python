"""
The main window has no keyboard shortcuts (2026-10-15, user's decision).

The **V** key used to toggle External Trigger. It called setChecked() on the
checkbox, which Qt allows even while the checkbox is disabled — so V during a
measurement switched the trigger: a camera restart mid-run, 5 s of PC
timestamps inside timeVec, and with the trigger On → Off the camera leaving
the Arduino's pulses for its own rate. Vika's MATLAB GUI (SCOS_GUI.m/.fig)
has no keyboard shortcuts at all, so V was removed rather than guarded.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

_app = QApplication.instance() or QApplication([])

from tests.test_results_schema import _window


@pytest.fixture
def window(monkeypatch):
    w = _window()
    calls = []
    monkeypatch.setattr(w.camera, "set_trigger",
                        lambda *a, **k: calls.append(("set_trigger", a)))
    monkeypatch.setattr(w, "_upload_arduino",
                        lambda *a, **k: calls.append(("upload", a)))
    yield w, calls
    w.close()


@pytest.mark.parametrize("locked", [False, True], ids=["preview", "measuring"])
def test_v_does_not_touch_the_trigger(window, locked):
    w, calls = window
    if locked:
        w._set_params_enabled(False)          # what Start SCOS does
    before = w.chk_trigger.isChecked()
    QTest.keyClick(w, Qt.Key.Key_V)
    QTest.keyClick(w, Qt.Key.Key_V, Qt.KeyboardModifier.ShiftModifier)
    assert w.chk_trigger.isChecked() == before
    assert calls == []


def test_the_window_has_no_key_handler_of_its_own():
    """A new shortcut must be a deliberate decision, with this test changed."""
    from gui.main_window import MainWindow
    assert "keyPressEvent" not in MainWindow.__dict__
