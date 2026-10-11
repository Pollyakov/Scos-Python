"""
"Cut Image" crops on the camera — the window side (todo U6, plan step 5).

Clicking "Cut Image" makes the camera itself send only a rectangle around the
ROI circle; "Full Image" goes back to the whole sensor. What these tests pin
down is what the window must do around that, because each slip here gives
wrong data without any error:

  * the ROI mask is rebuilt for the cropped frames at once, from the circle,
    which stays in sensor coordinates (plan 4.3, decision D3);
  * frames of the old size still queued after the click are skipped — they
    used to reset the mask to the whole frame, silently dropping the ROI
    (plan pitfall P1);
  * the crop the window uses is the one the camera reports, so a refused crop
    leaves everything on the full sensor (pitfall P4);
  * Cut/Full and the ROI are locked from Start SCOS until the run has fully
    ended, the laser-off check included (plan 4.6, decision D2), and the
    circle stays locked while the image is cut (decision D1).

The camera is a stand-in that cuts its frames with the same PlaybackCrop the
playback sources use, and asks for coarse steps (4/2) so that rounding shows.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QObject, pyqtSignal

# QApplication before gui.main_window — see tests/CLAUDE.md.
_app = QApplication.instance() or QApplication([])

from gui.main_window import MainWindow
from gui import safety_dialog
from core.crop import CROP_MARGIN_PX, CameraSteps, PlaybackCrop, circle_mask
from core.session import State


SENSOR_W, SENSOR_H = 300, 200
CIRCLE = (150.0, 100.0, 30.0)          # cx, cy, r — sensor pixels


class _CroppingCamera(QObject):
    """A camera that really crops, like FolderMockCamera, with coarse steps."""

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

    def __init__(self, refuse: bool = False):
        super().__init__()
        self.is_synthetic   = False
        self.frames_emitted = 0
        self.refuse         = refuse
        self.roi_position   = None
        self.applied_crop   = None
        self._crop          = PlaybackCrop()
        self._open          = False
        rng = np.random.default_rng(0)
        self.full_frame = rng.integers(100, 4000, (SENSOR_H, SENSOR_W)).astype(np.uint16)

    def start_capture(self):
        self._open = True
        self._reapply()

    def set_roi(self, x, y, w, h):
        self.roi_position = (x, y, w, h)
        return self._reapply()

    def clear_roi(self):
        self.roi_position = None
        return self._reapply()

    def camera_steps(self):
        return CameraSteps(4, 2, 4, 2, 4, 2)

    def sensor_size(self):
        return SENSOR_W, SENSOR_H

    def _reapply(self):
        if not self._open:
            return None
        if self.refuse and self.roi_position is not None:
            self.roi_position = None
            self.warning.emit("Camera refused the image crop — using the full sensor")
        self.applied_crop, _ = self._crop.apply(self.roi_position, SENSOR_W, SENSOR_H)
        return self.applied_crop

    def frame(self) -> np.ndarray:
        """The next frame as the camera sends it now — cut if a crop is set."""
        return self._crop.cut(self.full_frame)

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass

    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


def _circ():
    cx, cy, r = CIRCLE
    return {"cx": cx, "cy": cy, "r": r}


def _make_window(cam):
    w = MainWindow(camera=cam)
    w.btn_start_video.setChecked(True)          # Start Video → PREVIEW
    assert w._state == State.PREVIEW
    w._on_scos_frame(cam.frame(), 0.0)          # first frame, full sensor
    w.image_widget.update_frame(cam.frame())
    w.image_widget.set_roi_circle(*CIRCLE)
    return w


@pytest.fixture
def cam():
    return _CroppingCamera()


@pytest.fixture
def window(cam):
    w = _make_window(cam)
    yield w
    w._set_state(State.PREVIEW)
    w.close()


def _click_cut(w):
    assert w.image_widget.btn_cut_image.isEnabled()
    w.image_widget.btn_cut_image.click()


class TestCut:

    def test_cut_crops_the_camera_around_the_circle(self, window, cam):
        _click_cut(window)
        c = cam.applied_crop
        assert not c.is_full_sensor
        assert window.image_widget.is_cut
        assert window.image_widget.btn_cut_image.text() == "Full Image"
        # Coarse steps were obeyed (the request went through crop_for_circle).
        assert c.offset_x % 4 == 0 and c.width % 4 == 0
        assert c.offset_y % 2 == 0 and c.height % 2 == 0
        assert cam.frame().shape == c.shape
        # The full margin is kept around the circle (the κ² filter of the
        # largest window reads that far — plan pitfall P2). The test circle
        # is far enough from the sensor edges for all of it to fit.
        cx, cy, r = CIRCLE
        assert c.offset_x <= cx - r - CROP_MARGIN_PX
        assert c.offset_y <= cy - r - CROP_MARGIN_PX
        assert c.offset_x + c.width  >= cx + r + CROP_MARGIN_PX + 1
        assert c.offset_y + c.height >= cy + r + CROP_MARGIN_PX + 1
        assert not c.is_full_sensor

    def test_mask_is_rebuilt_for_the_crop_from_the_circle(self, window, cam):
        _click_cut(window)
        c = cam.applied_crop
        expected = circle_mask(c.shape, _circ(), (c.offset_x, c.offset_y))
        np.testing.assert_array_equal(window._mask, expected)
        # The same pixels as the full-sensor mask, cut out — nothing lost.
        full = circle_mask((SENSOR_H, SENSOR_W), _circ())
        np.testing.assert_array_equal(
            window._mask,
            full[c.offset_y:c.offset_y + c.height, c.offset_x:c.offset_x + c.width])
        assert window._mask.sum() == full.sum()
        # The circle keeps its sensor coordinates (decision D3 → Params.ROI).
        assert window._roi_circ == _circ()

    def test_processor_gets_the_cropped_mask(self, window, cam):
        _click_cut(window)
        roi = window.processor._roi_crop
        assert roi.mask_crop.sum() == window._mask.sum()
        assert window._mask.shape == cam.applied_crop.shape

    def test_size_label_shows_the_crop_and_the_sensor(self, window, cam):
        _click_cut(window)
        c = cam.applied_crop
        assert window.lbl_size.text() == (
            f"Size : {c.width}×{c.height} of {SENSOR_W}×{SENSOR_H}")

    def test_full_image_restores_the_full_sensor_mask(self, window, cam):
        _click_cut(window)
        _click_cut(window)                       # now labelled "Full Image"
        assert cam.applied_crop.is_full_sensor
        assert not window.image_widget.is_cut
        assert window.image_widget.btn_cut_image.text() == "Cut Image"
        np.testing.assert_array_equal(
            window._mask, circle_mask((SENSOR_H, SENSOR_W), _circ()))
        assert window.lbl_size.text() == f"Size : {SENSOR_W}×{SENSOR_H}"


class TestOldSizeFramesSkipped:
    """Pitfall P1: a frame captured before the click must not reset the ROI."""

    def test_old_size_frame_does_not_reset_the_mask(self, window, cam):
        _click_cut(window)
        mask_before = window._mask.copy()
        seen_before = window._frames_seen
        window._on_scos_frame(cam.full_frame, 1.0)      # still queued, old size
        np.testing.assert_array_equal(window._mask, mask_before)
        assert window._mask.sum() < window._mask.size    # still a circle, not "all"
        # Counted as received all the same — _flush_stale_frames relies on it.
        assert window._frames_seen == seen_before + 1
        assert window._frames_wrong_size == 1

    def test_old_size_frame_is_kept_from_the_calibration(self, window, cam):
        _click_cut(window)

        class _Collector:
            frames = []
            n_collected = n_target = 0
            done = False
            def add_frame(self, f): self.frames.append(f.shape)

        window._dark_cal_collector = _Collector()
        window._state = State.DARK_CAL          # direct: only the frame path is under test
        window._on_scos_frame(cam.full_frame, 1.0)
        window._on_scos_frame(cam.frame(), 2.0)
        window._state = State.PREVIEW
        assert _Collector.frames == [cam.applied_crop.shape]

    def test_right_size_frame_is_accepted_after_the_skip(self, window, cam, caplog):
        _click_cut(window)
        window._on_scos_frame(cam.full_frame, 1.0)
        window._on_scos_frame(cam.full_frame, 1.5)
        with caplog.at_level("INFO", logger="gui.main_window"):
            window._on_scos_frame(cam.frame(), 2.0)
        assert window._frames_wrong_size == 0
        assert window._mask.shape == cam.applied_crop.shape
        # One line in app.log says how many were skipped.
        assert "Skipped 2 frame(s) of the previous image size" in caplog.text

    def test_display_ignores_an_old_size_frame(self, window, cam):
        _click_cut(window)
        window.image_widget.update_frame(cam.frame())
        window.image_widget.update_frame(cam.full_frame)
        assert window.image_widget._frame.shape == cam.applied_crop.shape
        c = cam.applied_crop
        pos = window.image_widget.image_item.pos()
        assert (pos.x(), pos.y()) == (c.offset_x, c.offset_y)


class TestRefusedCrop:

    def test_refused_crop_leaves_the_full_sensor(self, monkeypatch):
        # The camera's warning opens a dialog in PREVIEW; answer it.
        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: warned.append(a[2])))
        cam = _CroppingCamera(refuse=True)
        w = _make_window(cam)
        try:
            _click_cut(w)
            assert cam.applied_crop.is_full_sensor
            assert not w.image_widget.is_cut
            assert w.image_widget.btn_cut_image.text() == "Cut Image"
            np.testing.assert_array_equal(
                w._mask, circle_mask((SENSOR_H, SENSOR_W), _circ()))
            # The ROI stays editable — nothing is cut.
            assert w.image_widget.btn_draw_roi.isEnabled()
            assert warned
        finally:
            w._set_state(State.PREVIEW)
            w.close()


class TestLocks:

    def test_cut_needs_a_circle(self, window):
        window.image_widget._clear_roi()
        assert not window.image_widget.btn_cut_image.isEnabled()

    def test_cut_needs_running_video(self, window):
        window._set_state(State.IDLE)
        assert not window.image_widget.btn_cut_image.isEnabled()

    def test_cut_unavailable_for_a_camera_that_cannot_crop(self, window, monkeypatch):
        monkeypatch.delattr(_CroppingCamera, "set_roi")
        window._set_state(State.PREVIEW)
        assert not window.image_widget.btn_cut_image.isEnabled()

    @pytest.mark.parametrize("state", [State.DARK_CAL, State.BRIGHT_CAL,
                                       State.MEASURING_INIT, State.MEASURING,
                                       State.FINISHED])
    def test_cut_and_roi_locked_during_a_run(self, window, state):
        window._set_state(state)
        iw = window.image_widget
        assert not iw.btn_cut_image.isEnabled()
        # Decision D2: the circle too, from the dark calibration on.
        assert not iw._roi_circle.isEnabled()
        assert not iw.btn_draw_roi.isEnabled()

    def test_full_image_also_locked_during_a_run(self, window):
        _click_cut(window)
        window._set_state(State.DARK_CAL)
        assert not window.image_widget.btn_cut_image.isEnabled()

    def test_circle_locked_while_cut(self, window):
        _click_cut(window)
        iw = window.image_widget
        # Decision D1: move the circle only after Full Image.
        assert not iw._roi_circle.isEnabled()
        for btn in (iw.btn_auto_roi, iw.btn_draw_roi, iw.btn_clear_roi):
            assert not btn.isEnabled()
        assert iw.btn_cut_image.isEnabled()          # Full Image is the way back

    def test_locked_through_the_laser_off_check(self, window, monkeypatch):
        """Stop SCOS unlocks the parameter fields before the laser-off check,
        which measures a fresh frame with the run's mask — Cut/Full must stay
        locked until the check is over (plan 4.6)."""
        _click_cut(window)
        iw = window.image_widget
        during = {}

        def _check(mask):
            during["cut"]    = iw.btn_cut_image.isEnabled()
            during["circle"] = iw._roi_circle.isEnabled()
            during["fields"] = window.spn_exposure.isEnabled()
            return None

        monkeypatch.setattr(window, "_laser_off_check", _check)
        monkeypatch.setattr(safety_dialog, "show_probe_removal", lambda *a, **k: None)
        window._set_params_enabled(False)
        window._set_state(State.MEASURING)
        window._toggle_scos(False)

        assert during == {"cut": False, "circle": False, "fields": True}
        assert window._state == State.PREVIEW
        # After the run: Full Image is available again, and the circle stays
        # locked because the image is still cut (D1).
        assert iw.btn_cut_image.isEnabled()
        assert iw.btn_cut_image.text() == "Full Image"
        assert not iw._roi_circle.isEnabled()


class TestStartVideo:

    def test_crop_kept_across_stop_and_start_video(self, window, cam):
        _click_cut(window)
        crop = cam.applied_crop
        window.btn_start_video.setChecked(False)
        window.btn_start_video.setChecked(True)
        assert window._camera_crop == crop
        assert window._mask.shape == crop.shape


class TestMaskMatWhileCut:
    """--mock-folder: the recording's Mask.mat arrives from the calibration
    loader a few seconds after Start Video. If the operator has cut the image
    by then, the full-sensor Mask.mat must not meet cropped frames (P1)."""

    def test_mask_mat_goes_back_to_the_full_image_first(self, window, cam, tmp_path):
        import scipy.io
        _click_cut(window)
        assert window.image_widget.is_cut
        cx, cy, r = 100.0, 80.0, 40.0             # Mask.mat's circle, elsewhere
        tot = circle_mask((SENSOR_H, SENSOR_W), {"cx": cx, "cy": cy, "r": r})
        path = tmp_path / "Mask.mat"
        scipy.io.savemat(str(path), {
            "totMask": tot.astype(np.uint8),
            "channels": {"Centers": np.array([[cx, cy]]), "Radii": np.array([[r]])},
        })
        window._pending_mask_mat = path
        window._on_calibration_done(True, "ok")

        assert not window.image_widget.is_cut
        assert cam.applied_crop.is_full_sensor
        assert window.image_widget.roi_circle == {"cx": cx, "cy": cy, "r": r}
        np.testing.assert_array_equal(window._mask, tot)
