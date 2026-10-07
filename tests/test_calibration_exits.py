"""
Leaving the calibration sequence early puts everything back (todo K2, K3).

There are five early exits — Cancel at the bright prompt, an error from the
dark or the bright collector, and Stop SCOS during DARK_CAL or BRIGHT_CAL
(Stop Video presses Stop SCOS, so it is a sixth route to the same place). Each
used to undo its own subset of what Start SCOS had changed:

  K2  after a Cancel or an error the parameter boxes stayed locked until the
      next full run, and the session folder stayed on disk with a
      Calibration.h5 holding only the dark group;
  K3  a stop during dark calibration left --mock-folder playback on the dark
      folder, and a dark-calibration error also left the external trigger off.

All of them now go through MainWindow._abandon_calibration(). The folder of
such a run is removed when empty and renamed `<name>_cancelled` when it already
holds a partial calibration (the user's choice, 2026-10-07).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core.session import State
from gui.main_window import MainWindow


class _FakeCamera(QObject):
    """Records the calls that matter here: trigger and playback source."""

    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)

    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def __init__(self):
        super().__init__()
        self.trigger_calls = []
        self.playback_source = "main"

    def set_trigger(self, on, delay=0):
        self.trigger_calls.append(on)

    def set_playback_source(self, source):
        changed = source != self.playback_source
        self.playback_source = source
        return changed

    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k): pass
    def stop(self, *a, **k):          pass
    def close(self, *a, **k):         pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


class _FailingCollector:
    """A collector that is full but cannot produce a result."""
    n_collected = 3
    n_target    = 3
    done        = True
    window_size = 7

    def add_frame(self, frame):
        pass

    def result(self, **kwargs):
        raise RuntimeError("not enough frames")


@pytest.fixture
def cam():
    return _FakeCamera()


@pytest.fixture
def win(cam):
    w = MainWindow(camera=cam)
    yield w
    w.close()


def _mid_run(w, tmp_path, state, *, dark_written, trigger_was_on=False):
    """Put the window where Start SCOS leaves it partway through calibration."""
    folder = tmp_path / "subject_20261007_120000"
    folder.mkdir()
    if dark_written:
        (folder / "Calibration.h5").write_bytes(b"dark only")
    w._session_folder = folder
    w._dark_cal_trigger_was_on = trigger_was_on
    w._set_params_enabled(False)
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.setText("Stop SCOS")
    w.btn_start_scos.blockSignals(False)
    w.btn_save.setEnabled(False)
    if state is State.DARK_CAL:
        w._set_playback_source("dark")
    w._set_state(state)
    return folder


def _assert_back_to_preview(w, cam):
    assert w._state is State.PREVIEW
    assert w.spn_exposure.isEnabled() and w.txt_recording_name.isEnabled(), (
        "the parameter boxes are still locked (K2)")
    assert not w.btn_start_scos.isChecked()
    assert w.btn_start_scos.text() == "Start SCOS"
    assert w.btn_save.isEnabled()
    assert cam.playback_source == "main", "playback left on the dark folder (K3)"
    assert w._dark_cal_collector is None and w._bright_cal_collector is None
    assert w._session_folder is None


def _assert_renamed(folder):
    renamed = folder.with_name(folder.name + "_cancelled")
    assert not folder.exists()
    assert (renamed / "Calibration.h5").read_bytes() == b"dark only", (
        "the partial calibration must be kept, under a name that says so")


class TestCancelAtTheBrightPrompt:

    def test_unlocks_and_renames_the_folder(self, win, cam, tmp_path, monkeypatch):
        folder = _mid_run(win, tmp_path, State.PREVIEW, dark_written=True)
        monkeypatch.setattr(QMessageBox, "question", staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Cancel))

        win._start_bright_cal()

        _assert_back_to_preview(win, cam)
        _assert_renamed(folder)
        assert "_cancelled" in win.status.currentMessage()
        assert cam.trigger_calls == [], (
            "_finish_dark_cal already restored the trigger; setting it again "
            "would restart a real camera's grabbing")


class TestCalibrationErrors:

    def test_dark_error_restores_everything(self, win, cam, tmp_path, monkeypatch):
        folder = _mid_run(win, tmp_path, State.DARK_CAL, dark_written=False,
                          trigger_was_on=True)
        win._dark_cal_collector = _FailingCollector()
        shown = []
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(
            lambda *a, **k: shown.append(a[1])))

        win._finish_dark_cal()

        assert shown == ["Dark Calibration Error"]
        _assert_back_to_preview(win, cam)
        assert cam.trigger_calls == [True], "external trigger left off"
        assert not folder.exists(), "the empty folder should have been removed"

    def test_bright_error_restores_everything(self, win, cam, tmp_path, monkeypatch):
        folder = _mid_run(win, tmp_path, State.BRIGHT_CAL, dark_written=True,
                          trigger_was_on=True)
        win._bright_cal_collector = _FailingCollector()
        shown = []
        monkeypatch.setattr(QMessageBox, "critical", staticmethod(
            lambda *a, **k: shown.append(a[1])))

        win._finish_bright_cal()

        assert shown == ["Bright Calibration Error"]
        _assert_back_to_preview(win, cam)
        assert cam.trigger_calls == []
        _assert_renamed(folder)

    @pytest.mark.parametrize("state, collector_attr, finish", [
        (State.DARK_CAL,   "_dark_cal_collector",   "_finish_dark_cal"),
        (State.BRIGHT_CAL, "_bright_cal_collector", "_finish_bright_cal"),
    ])
    def test_frames_during_the_error_dialog_open_no_second_one(
            self, win, cam, tmp_path, monkeypatch, state, collector_attr, finish):
        """A real modal dialog keeps delivering frames. Shown while the state
        was still *_CAL with a full collector, each frame re-ran the finish
        method and opened another dialog on top of the first."""
        _mid_run(win, tmp_path, state, dark_written=state is State.BRIGHT_CAL)
        setattr(win, collector_attr, _FailingCollector())
        shown = []

        def _critical(*a, **k):
            shown.append(a[1])
            for _ in range(3):        # what the nested event loop would deliver
                win._on_scos_frame(np.zeros((16, 16), dtype=np.uint16), 0.0)

        monkeypatch.setattr(QMessageBox, "critical", staticmethod(_critical))

        getattr(win, finish)()

        assert len(shown) == 1, f"{len(shown)} error dialogs opened"


class TestStoppingDuringCalibration:

    def test_stop_scos_during_dark_cal(self, win, cam, tmp_path):
        folder = _mid_run(win, tmp_path, State.DARK_CAL, dark_written=False,
                          trigger_was_on=True)

        win.btn_start_scos.setChecked(False)

        _assert_back_to_preview(win, cam)
        assert cam.trigger_calls == [True]
        assert not folder.exists()

    def test_stop_scos_during_bright_cal(self, win, cam, tmp_path):
        folder = _mid_run(win, tmp_path, State.BRIGHT_CAL, dark_written=True)

        win.btn_start_scos.setChecked(False)

        _assert_back_to_preview(win, cam)
        assert cam.trigger_calls == []
        _assert_renamed(folder)

    def test_stop_video_during_dark_cal(self, win, cam, tmp_path):
        """Stop Video presses Stop SCOS; the cancellation must still happen."""
        win.btn_start_video.blockSignals(True)
        win.btn_start_video.setChecked(True)
        win.btn_start_video.blockSignals(False)
        folder = _mid_run(win, tmp_path, State.DARK_CAL, dark_written=False)

        win.btn_start_video.setChecked(False)

        assert cam.playback_source == "main", "playback left on the dark folder (K3)"
        assert win.spn_exposure.isEnabled()
        assert win._state is State.IDLE
        assert not folder.exists()

    def test_closing_the_window_during_bright_cal(self, cam, tmp_path):
        """closeEvent does not go through Stop SCOS; the folder must still be
        marked as a cancelled run."""
        w = MainWindow(camera=cam)
        folder = _mid_run(w, tmp_path, State.BRIGHT_CAL, dark_written=True)

        w.close()

        _assert_renamed(folder)
