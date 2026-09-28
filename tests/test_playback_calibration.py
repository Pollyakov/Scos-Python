"""
Tests for rehearsing a calibration in `--mock-folder` playback — todo D6.

Playback has no laser to switch off. Before this, Start SCOS's dark
calibration therefore collected 60 frames of the laser-on recording, and two
faults followed at once: `dark_var` carried the live signal's variance, and
`dark_mean` was in raw TIFF units while `process()` divides the frame by 64.
Corrected κ² came out negative in every frame, the run aborted, and the one
mode that exists to rehearse a session could not reach MEASURING.

Both halves are fixed here:

  * `FolderMockCamera.set_playback_source("dark")` plays the `_dark` folder
    while the app is in DARK_CAL, so the collector gets frames that really are
    dark. Every prompt, collector, dialog and file write still runs.
  * `MainWindow._to_du()` converts a frame to the units `process()` works in
    before the collectors see it.

Measured on the lab recording, 2026-09-28: the fixed path gives κ²_corr ≈
+0.0090 against MATLAB's 0.0105. Positive, so a rehearsal completes — but 14 %
low, because the bright calibration still has to come from the main recording,
which was made with a subject in place. `spVar` is then speckle rather than the
illumination profile, 2.3× the value in `smoothingCoefficients.mat`. That gap
is a property of the dataset, not of the code: on the rig the operator removes
the subject. The offline tests remain the accuracy check; this mode is for
rehearsing the sequence.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_app = QApplication.instance() or QApplication([])

from core.session import State
from folder_camera import FolderMockCamera
from gui.main_window import MainWindow

SHIFT = 6          # 10-bit left-justified in uint16, as Pylon Viewer writes it
DARK_DU, MAIN_DU = 100, 500


def _write_tiffs(folder: Path, n: int, value_du: int):
    """Write n frames whose DU value is `value_du`, stored left-justified."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        frame = np.full((16, 16), value_du << SHIFT, dtype=np.uint16)
        # One varying pixel, so the variance is not identically zero.
        frame[0, 0] = (value_du + i % 3) << SHIFT
        tifffile.imwrite(str(folder / f"Basler_x__1__f_{i:04d}.tiff"), frame)


@pytest.fixture
def recording(tmp_path):
    """A recording folder with the `_dark` sibling, nested as Pylon writes it."""
    main = tmp_path / "expT5ms_Gain24dB_BL100DU_FR40Hz_001"
    _write_tiffs(main, 6, MAIN_DU)
    dark_outer = tmp_path / (main.name + "_dark")
    _write_tiffs(dark_outer / (main.name + "_dark"), 6, DARK_DU)
    return main


@pytest.fixture
def recording_without_dark(tmp_path):
    main = tmp_path / "expT5ms_Gain24dB_BL100DU_FR40Hz_002"
    _write_tiffs(main, 6, MAIN_DU)
    return main


class TestPlaybackSource:
    """Switching the file list the playback thread reads from."""

    def test_starts_on_the_recording(self, recording):
        cam = FolderMockCamera(recording)
        cam.open()
        assert cam.playback_source == "main"

    def test_switches_to_dark_and_back(self, recording):
        cam = FolderMockCamera(recording)
        cam.open()
        assert cam.set_playback_source("dark") is True
        assert cam.playback_source == "dark"
        assert cam.set_playback_source("main") is True
        assert cam.playback_source == "main"

    def test_refuses_and_warns_when_there_is_no_dark_folder(
            self, recording_without_dark):
        cam = FolderMockCamera(recording_without_dark)
        cam.open()
        seen = []
        cam.warning.connect(seen.append)

        assert cam.set_playback_source("dark") is False
        assert cam.playback_source == "main", (
            "playback must not stop — a frameless run is worse than a warned one"
        )
        assert len(seen) == 1
        assert "_dark" in seen[0]

    def test_unknown_source_is_a_programming_error(self, recording):
        cam = FolderMockCamera(recording)
        cam.open()
        with pytest.raises(ValueError):
            cam.set_playback_source("bright")

    def test_the_dark_folder_is_found_through_pylon_nesting(self, recording):
        cam = FolderMockCamera(recording)
        cam.open()
        assert len(cam._dark_files) == 6, (
            "the dark TIFFs live one level deeper than the folder named _dark"
        )

    def test_emitted_frames_follow_the_source(self, recording, qapp_processing):
        cam = FolderMockCamera(recording, loop=True)
        cam.open()
        frames = []
        cam.frame_ready.connect(lambda f, t: frames.append(int(f[8, 8])))
        cam.frame_rate = 200.0

        cam.set_playback_source("dark")
        cam.start_capture()
        qapp_processing(lambda: len(frames) >= 3)
        n_dark = len(frames)

        cam.set_playback_source("main")
        qapp_processing(lambda: len(frames) >= n_dark + 3)
        cam.stop()

        assert frames[0] == DARK_DU << SHIFT
        assert frames[-1] == MAIN_DU << SHIFT


@pytest.fixture
def qapp_processing():
    """Spin the event loop until `ready()` or a timeout, without sleeping blind."""
    import time

    def _wait(ready, timeout_s=5.0):
        deadline = time.monotonic() + timeout_s
        while not ready():
            if time.monotonic() > deadline:
                raise AssertionError("timed out waiting for playback frames")
            _app.processEvents()
            time.sleep(0.005)
    return _wait


class TestFrameUnits:
    """`_to_du` — the other half of the bug."""

    @pytest.fixture
    def window(self, monkeypatch, tmp_path):
        # Filling the dark collector chains into _start_bright_cal, which
        # prompts for the laser; conftest turns an unstubbed prompt into a
        # failure so the suite cannot hang on it.
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: str(tmp_path)))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        w = MainWindow(camera=_StubCamera())
        yield w
        w.close()

    def test_a_real_camera_frame_is_untouched(self, window):
        window.processor.scale = 1.0
        frame = np.full((4, 4), 500, dtype=np.uint16)
        assert window._to_du(frame) is frame, "no copy, no arithmetic, no cost"

    def test_a_left_justified_frame_is_divided(self, window):
        window.processor.scale = 64.0
        frame = np.full((4, 4), 100 << SHIFT, dtype=np.uint16)
        np.testing.assert_allclose(window._to_du(frame), 100.0)

    def test_the_collector_ends_up_in_du(self, window):
        # The failure this prevents: dark_mean 64x too large, which made the
        # recorded mean ROI intensity come out at -7872 DU.
        from core.session import DarkCalCollector

        window.processor.scale = 64.0
        window._mask = np.ones((4, 4), dtype=bool)
        window._dark_cal_collector = DarkCalCollector(2, 3)
        window._set_state(State.DARK_CAL)
        for _ in range(2):
            window._on_scos_frame(np.full((4, 4), 100 << SHIFT, dtype=np.uint16), 0.0)

        assert window.processor.dark_mean is not None
        assert float(np.mean(window.processor.dark_mean)) == pytest.approx(100.0)


class _StubCamera(QObject):
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

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


class TestMainWindowDrivesTheSwitch:
    """The GUI has to ask for the switch, and only of a camera that has one."""

    @pytest.fixture
    def window(self, recording, monkeypatch, tmp_path):
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: str(tmp_path)))
        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        cam = FolderMockCamera(recording)
        cam.open()
        w = MainWindow(camera=cam)
        yield w
        w.close()

    def test_dark_calibration_plays_the_dark_folder(self, window):
        window._start_dark_cal()
        assert window._state is State.DARK_CAL
        assert window.camera.playback_source == "dark"

    def test_playback_returns_to_the_recording_before_bright_cal(self, window):
        window._start_dark_cal()
        # Feed the collector to completion; _finish_dark_cal runs from there
        # and chains straight into _start_bright_cal. A few more frames than
        # n_target, because _flush_stale_frames discards the first arrivals.
        frame = np.full((16, 16), DARK_DU << SHIFT, dtype=np.uint16)
        for _ in range(window._dark_cal_collector.n_target + 20):
            window._on_scos_frame(frame, 0.0)
            if window._state is not State.DARK_CAL:
                break

        assert window._state is State.BRIGHT_CAL
        assert window.camera.playback_source == "main", (
            "bright calibration must not be fed dark frames"
        )

    def test_a_camera_without_the_hook_is_left_alone(self):
        # _set_playback_source is called unconditionally; a real Basler has no
        # such method and must not raise.
        w = MainWindow(camera=_StubCamera())
        try:
            w._set_playback_source("dark")   # no exception is the assertion
        finally:
            w.close()


class TestRoiFromMaskMat:
    """The ROI circle stored alongside a recording.

    MATLAB's `imfindcircles` returns centers as [x y]. Reading them as [y x]
    put the circle at (684, 1215) on a 1216-row frame — centred on the bottom
    edge — and the mask generated from it overwrites `totMask` through the
    `roi_changed` signal, so every κ² in a replayed session was computed over
    roughly the wrong half of the sensor.

    Measured on the lab recording: the swapped reading agrees with `totMask`
    on 49.4 % of pixels, the correct one on 99.3 %.
    """

    def test_centers_are_read_as_x_then_y(self, tmp_path, monkeypatch):
        import scipy.io

        h, w = 40, 60
        cx, cy, r = 45.0, 12.0, 15.0          # clearly not interchangeable
        yy, xx = np.ogrid[:h, :w]
        tot = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r

        mask_mat = tmp_path / "Mask.mat"
        scipy.io.savemat(str(mask_mat), {
            "totMask": tot.astype(np.uint8),
            "channels": {"Centers": np.array([[cx, cy]]),
                         "Radii":   np.array([[r]])},
        })

        monkeypatch.setattr(QMessageBox, "question",
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
        w_ = MainWindow(camera=_StubCamera())
        try:
            # A frame has to be on screen, or the widget cannot turn a circle
            # into a mask and emits nothing.
            w_.image_widget.update_frame(np.zeros((h, w), dtype=np.uint16))
            w_._pending_mask_mat = mask_mat
            w_._on_calibration_done(True, "test")

            assert w_._roi_circ["cx"] == pytest.approx(cx)
            assert w_._roi_circ["cy"] == pytest.approx(cy)
            # And the mask the circle produces must be the one the file meant.
            agreement = float((w_._mask == tot).mean())
            assert agreement > 0.98, (
                f"circle-derived mask agrees with totMask on only "
                f"{agreement*100:.1f} % of pixels"
            )
        finally:
            w_.close()
