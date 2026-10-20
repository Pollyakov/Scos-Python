"""
The "Save Frames" checkbox through the window — docs/Plan_Frame_Saving.md.

Ticked, a run saves every dark, bright and measurement frame as one TIFF per
frame in `<session>/Frames/`, in the layout Vika's MATLAB script reads. What
the window is held to:

  - the free-space check refuses a run whose frames would not fit, before any
    folder is created (decisions 3 and 4);
  - the saved calibration frames are exactly those the calibrations used — not
    the dark frames skipped while the GUI thread caught up;
  - the measurement frames go through the pipeline's frame sink (camera
    thread), and the counts land in the results file's metadata;
  - every way out closes the writer; an abandoned run keeps its saved frames
    with the partial calibration (`_cancelled`);
  - a nearly full disk stops the measurement cleanly.
"""

import csv
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

_app = QApplication.instance() or QApplication([])

from core import disk_space
from core.session import State
from gui.main_window import MainWindow

N = 10                                    # the Dark/Bright Frames boxes' minimum
SHAPE = (16, 20)
FOLDER = "expT8ms_Gain8dB_FR20Hz_BL100DU"


class _FakeCamera(QObject):
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
    black_level   = 100.0

    def __init__(self):
        super().__init__()
        self.frames_emitted = 0

    def set_max_frame_rate(self, enabled): return None
    def set_trigger(self, *a, **k):      pass
    def stop(self, *a, **k):             pass
    def set_playback_source(self, source): return False
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40075248", "model": "a2A1920-160umBAS",
                "width": SHAPE[1], "height": SHAPE[0]}


@pytest.fixture
def cam():
    return _FakeCamera()


@pytest.fixture
def dialogs(monkeypatch):
    """OK at both laser prompts; every critical dialog recorded."""
    shown = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "critical",
                        staticmethod(lambda parent, title, text, *a, **k: shown.append(title)))
    return shown


@pytest.fixture
def win(cam, dialogs, tmp_path, monkeypatch):
    w = MainWindow(camera=cam)
    w._output_root = tmp_path
    w.spn_exposure.setValue(8.0)
    w.spn_gain.setValue(8.0)
    w.spn_fps.setValue(20.0)
    w.spn_n1.setValue(N)
    w.spn_n2.setValue(N)
    w.spn_duration.setValue(1.0)
    w.chk_save_frames.setChecked(True)
    yield w
    w._close_frame_writer()
    if w._recorder is not None:
        w._recorder.close()
        w._recorder = None
    w._set_state(State.PREVIEW)
    w.close()


def _press_start(w):
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.setText("Stop SCOS")
    w.btn_start_scos.blockSignals(False)
    w._start_dark_cal()


def _feed(w, cam, n, behind=0, value=100):
    # The flush at each laser prompt discards the next arrivals as captured
    # before it; these tests deliver only post-prompt frames.
    w._skip_frames_until = 0
    for i in range(n):
        cam.frames_emitted = w._frames_seen + 1 + behind
        w._on_scos_frame(np.full(SHAPE, value + i, dtype=np.uint16), 0.01 * w._frames_seen)


def _start(w, cam):
    _press_start(w)
    assert w._state is State.DARK_CAL
    w._skip_frames_until = 0


def _frames_dir(w_folder):
    return w_folder / "Frames"


class TestCheckbox:
    def test_enabled_and_unticked_at_startup(self, cam, dialogs):
        w = MainWindow(camera=cam)
        try:
            assert w.chk_save_frames.isEnabled()
            assert not w.chk_save_frames.isChecked()
            assert not w.chk_save_frames.isHidden()       # the protocol lists it
        finally:
            w.close()

    def test_locked_during_a_run(self, win):
        win._set_params_enabled(False)
        assert not win.chk_save_frames.isEnabled()
        win._set_params_enabled(True)
        assert win.chk_save_frames.isEnabled()

    def test_not_a_remembered_setting(self, win):
        # A box that fills a disk in an hour must never come back on by itself.
        assert not any("frame" in k.lower() and "save" in k.lower()
                       for k in win._current_settings())


class TestCalibrationFrames:
    def test_dark_and_bright_frames_saved_in_their_folders(self, win, cam, monkeypatch):
        monkeypatch.setattr(win, "_enable_measurement_intake", lambda mask: None)
        _start(win, cam)
        session = win._session_folder
        _feed(win, cam, N)                                # dark
        assert win._state is State.BRIGHT_CAL
        _feed(win, cam, N, value=2000)                    # bright → measurement
        assert win._state is State.MEASURING_INIT
        win._close_frame_writer()
        frames = _frames_dir(session)
        dark = sorted(os.listdir(frames / f"{FOLDER}_dark"))
        bright = sorted(os.listdir(frames / f"{FOLDER}_bright"))
        assert len(dark) == N and len(bright) == N
        assert dark[0].startswith("Basler_a2A1920-160umBAS__40075248__")
        import tifffile
        assert tifffile.imread(frames / f"{FOLDER}_bright" / bright[0])[0, 0] == 2000

    def test_skipped_dark_frames_are_not_saved(self, win, cam):
        _start(win, cam)
        session = win._session_folder
        _feed(win, cam, 1)                                # sets the backlog floor
        _feed(win, cam, 4, behind=win._DARK_MAX_BACKLOG + 5)   # skipped
        assert win._dark_skipped == 4
        _feed(win, cam, 3)
        win._close_frame_writer()
        assert len(os.listdir(_frames_dir(session) / f"{FOLDER}_dark")) == 4

    def test_raw_frame_saved_not_the_du_one(self, win, cam, monkeypatch):
        # In playback process() divides by 64; the file must keep what the
        # camera sent, so the saved session replays like the original.
        monkeypatch.setattr(win.processor, "scale", 64.0)
        _start(win, cam)
        session = win._session_folder
        _feed(win, cam, 1, value=6400)
        win._close_frame_writer()
        import tifffile
        (name,) = os.listdir(_frames_dir(session) / f"{FOLDER}_dark")
        assert tifffile.imread(_frames_dir(session) / f"{FOLDER}_dark" / name)[0, 0] == 6400


class TestMeasurementFrames:
    def test_frames_go_through_the_pipeline_sink(self, win, cam, monkeypatch):
        captured = {}
        monkeypatch.setattr(win._scos_worker, "enable_intake",
                            lambda mask, **kw: captured.update(kw))
        _start(win, cam)
        session = win._session_folder
        _feed(win, cam, N)
        _feed(win, cam, N)
        sink = captured["frame_sink"]
        assert sink is not None
        for i in range(5):                                 # as the camera thread would
            sink(np.full(SHAPE, 7, dtype=np.uint16), 100.0 + 0.05 * i, 0.05 * i)
        win._finish_frame_saving()
        assert len(os.listdir(_frames_dir(session) / FOLDER)) == 5
        with open(_frames_dir(session) / "frame_times.csv", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["phase"] == "measurement"]
        assert [float(r["timeVec_s"]) for r in rows] == pytest.approx([0, .05, .1, .15, .2])

    def test_counts_in_the_results_file(self, win, cam, monkeypatch):
        captured = {}
        monkeypatch.setattr(win._scos_worker, "enable_intake",
                            lambda mask, **kw: captured.update(kw))
        _start(win, cam)
        _feed(win, cam, N)
        _feed(win, cam, N)
        assert win._recorder is not None
        for i in range(3):
            captured["frame_sink"](np.zeros(SHAPE, np.uint16), 1.0 + i, float(i))
        win._finish_frame_saving()
        meta = dict(win._recorder.file["metadata"].attrs)
        assert meta["frames_folder"] == f"Frames/{FOLDER}"
        assert (meta["frames_saved_dark"], meta["frames_saved_bright"],
                meta["frames_saved_measurement"]) == (N, N, 3)
        assert meta["frames_not_saved_measurement"] == 0
        assert win._frame_writer is None

    def test_finishing_the_session_closes_the_writer_and_records(self, win, cam, monkeypatch):
        captured = {}
        monkeypatch.setattr(win._scos_worker, "enable_intake",
                            lambda mask, **kw: captured.update(kw))
        _start(win, cam)
        _feed(win, cam, N)
        _feed(win, cam, N)
        writer = win._frame_writer
        captured["frame_sink"](np.zeros(SHAPE, np.uint16), 1.0, 0.0)
        win._set_state(State.FINISHED)
        win._finish_session()              # the one finalization point
        assert win._frame_writer is None and writer._csv_file.closed
        assert dict(win._recorder.file["metadata"].attrs)["frames_saved_measurement"] == 1

    def test_without_the_box_nothing_is_saved(self, win, cam, monkeypatch):
        win.chk_save_frames.setChecked(False)
        captured = {}
        monkeypatch.setattr(win._scos_worker, "enable_intake",
                            lambda mask, **kw: captured.update(kw))
        _start(win, cam)
        session = win._session_folder
        _feed(win, cam, N)
        _feed(win, cam, N)
        assert captured["frame_sink"] is None
        assert not _frames_dir(session).exists()
        assert "frames_saved_dark" not in dict(win._recorder.file["metadata"].attrs)


class TestWaysOut:
    def test_cancel_at_the_laser_prompt_leaves_no_folder(self, win, cam, monkeypatch, tmp_path):
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
        _press_start(win)
        assert win._frame_writer is None
        assert list(tmp_path.iterdir()) == []

    def test_stop_during_dark_keeps_the_frames_with_the_partial_run(self, win, cam, tmp_path):
        _start(win, cam)
        name = win._session_folder.name
        _feed(win, cam, 3)
        win.btn_start_scos.setChecked(False)              # Stop SCOS
        assert win._frame_writer is None
        kept = tmp_path / f"{name}_cancelled"
        assert len(os.listdir(kept / "Frames" / f"{FOLDER}_dark")) == 3

    def test_closing_the_window_mid_calibration_closes_the_writer(self, cam, dialogs, tmp_path):
        w = MainWindow(camera=cam)
        w._output_root = tmp_path
        w.spn_n1.setValue(N)
        w.chk_save_frames.setChecked(True)
        _start(w, cam)
        writer = w._frame_writer
        _feed(w, cam, 2)
        w.close()
        assert writer._csv_file.closed
        assert any(p.name.endswith("_cancelled") for p in tmp_path.iterdir())


class TestPathTooLong:
    def test_refused_with_a_message_and_nothing_left(self, win, dialogs, monkeypatch, tmp_path):
        import core.frame_writer as fw
        monkeypatch.setattr(fw, "MAX_PATH_CHARS", 20)
        _press_start(win)
        assert dialogs == ["Cannot Save Frames"]
        assert win._state is not State.DARK_CAL and win._frame_writer is None
        assert not win.btn_start_scos.isChecked()
        assert list(tmp_path.iterdir()) == []             # the empty folder removed


class TestDiskSpace:
    def test_refused_when_the_frames_do_not_fit(self, win, dialogs, monkeypatch, tmp_path):
        monkeypatch.setattr(disk_space, "free_bytes", lambda path: 1 * disk_space.GB)
        _press_start(win)
        assert dialogs == ["Not Enough Disk Space"]
        assert win._state is not State.DARK_CAL
        assert not win.btn_start_scos.isChecked()
        assert list(tmp_path.iterdir()) == []             # no folder created

    def test_started_when_they_fit(self, win, dialogs, monkeypatch):
        monkeypatch.setattr(disk_space, "free_bytes", lambda path: 100 * disk_space.GB)
        _press_start(win)
        assert dialogs == []
        assert win._state is State.DARK_CAL

    def test_no_check_without_the_box(self, win, dialogs, monkeypatch):
        win.chk_save_frames.setChecked(False)
        monkeypatch.setattr(disk_space, "free_bytes", lambda path: 0)
        _press_start(win)
        assert win._state is State.DARK_CAL

    def test_unlimited_duration_counts_as_four_hours(self, win, dialogs, monkeypatch):
        # 16 x 20 frames: 4 h at 20 Hz = 288 000 frames ≈ 2.5 GB, plus 5 GB margin.
        win.spn_duration.setValue(0.0)
        need = disk_space.estimate(SHAPE[1], SHAPE[0], 2, 20, 4 * 3600, N, N)
        monkeypatch.setattr(disk_space, "free_bytes",
                            lambda path: need.bytes_required_free - 1)
        _press_start(win)
        assert dialogs == ["Not Enough Disk Space"]

    def test_nearly_full_disk_stops_the_measurement(self, win, cam, monkeypatch):
        monkeypatch.setattr(win, "_enable_measurement_intake", lambda mask: None)
        _start(win, cam)
        _feed(win, cam, N)
        _feed(win, cam, N)
        assert win._state is State.MEASURING_INIT
        stops = []
        monkeypatch.setattr(win.btn_start_scos, "setChecked", lambda on: stops.append(on))
        monkeypatch.setattr(disk_space, "free_bytes", lambda path: disk_space.GB)
        win._check_frame_saving()                         # within 30 s: not checked yet
        assert stops == []
        win._last_disk_check -= disk_space.CHECK_INTERVAL_S
        win._check_frame_saving()
        assert stops == [False]

    def test_not_checked_during_the_calibrations(self, win, cam, monkeypatch):
        _start(win, cam)
        stops = []
        monkeypatch.setattr(win.btn_start_scos, "setChecked", lambda on: stops.append(on))
        monkeypatch.setattr(disk_space, "free_bytes", lambda path: 0)
        win._last_disk_check -= disk_space.CHECK_INTERVAL_S
        win._check_frame_saving()
        assert stops == []


class TestNotSavedWarning:
    def test_reported_in_the_status_bar(self, win, cam):
        _start(win, cam)
        win._frame_writer.skipped["dark"] += 2            # as a full queue would
        win._check_frame_saving()
        assert "2 frame(s) could not be saved" in win.status.currentMessage()
