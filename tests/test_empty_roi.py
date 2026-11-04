"""
An ROI circle with no pixel to measure (todo B5, found 2026-10-11, reproduced 2026-11-04).

The circle can be dragged anywhere, also wholly off the image. Before this fix:
  * the intensity labels (⟨I⟩, p5, p95) ran np.percentile over zero pixels and
    raised IndexError every 0.5 s — in the app an exception in a slot shows the
    crash window and closes SCOS, in PREVIEW, before anything is measured;
  * Start SCOS went through both calibrations and only then measured over an
    empty mask; a circle too small for the window (radius 2 with window 7: 13 px
    on the image, 0 after the edge trim) did the same;
  * Cut Image cut the camera to a few pixels in a sensor corner.
Now the labels show a dash, and Start SCOS and Cut Image refuse.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QMessageBox

# QApplication before gui.main_window — see tests/CLAUDE.md.
_app = QApplication.instance() or QApplication([])

from core.session import State
from tests.test_cut_image import CIRCLE, _CroppingCamera, _make_window

OFF_IMAGE = (1000.0, 1000.0, 30.0)     # the test sensor is 300 × 200
TINY      = (150.0, 100.0, 2.0)        # on the image, nothing left after the trim
HALF_OFF  = (0.0, 100.0, 30.0)         # half on the image: a valid ROI


@pytest.fixture
def cam():
    return _CroppingCamera()


@pytest.fixture
def window(cam):
    w = _make_window(cam)
    yield w
    w._set_state(State.PREVIEW)
    w.close()


@pytest.fixture
def warnings_shown(monkeypatch):
    seen = []

    def _warning(parent, title, text, *a, **k):
        seen.append((title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_warning))
    return seen


@pytest.fixture
def gain_reached(window, monkeypatch):
    """Records whether Start SCOS got past the ROI check to the gain step
    (the first step of the protocol's dialogs), and stops the run there."""
    calls = []

    def _prepare_gain():
        calls.append(True)
        return False                    # refuse here: nothing else is started

    monkeypatch.setattr(window, "_prepare_gain", _prepare_gain)
    return calls


def _stats_update(w, cam):
    w._last_stats_time = 0.0            # labels are refreshed every 0.5 s
    w._on_scos_frame(cam.frame(), 1.0)


class TestLabels:

    def test_off_image_circle_does_not_raise(self, window, cam):
        window.image_widget.set_roi_circle(*OFF_IMAGE)
        assert not window._mask.any()
        _stats_update(window, cam)       # IndexError before the fix
        assert "off the image" in window.lbl_mean_i.text()
        assert window.lbl_p5.text().endswith("—")

    def test_labels_come_back_when_the_circle_is_back(self, window, cam):
        window.image_widget.set_roi_circle(*OFF_IMAGE)
        _stats_update(window, cam)
        window.image_widget.set_roi_circle(*CIRCLE)
        _stats_update(window, cam)
        assert window.lbl_mean_i.text().endswith("DU")


class TestStartScos:

    @pytest.mark.parametrize("circle", [OFF_IMAGE, TINY], ids=["off-image", "tiny"])
    def test_refused_before_anything_starts(self, window, warnings_shown, gain_reached,
                                            circle):
        window.image_widget.set_roi_circle(*circle)
        window.btn_start_scos.setChecked(True)
        assert [t for t, _ in warnings_shown] == ["ROI Outside the Image"]
        assert "Window Size 7" in warnings_shown[0][1]
        assert gain_reached == []        # refused before G, the folder and the laser
        assert window._state == State.PREVIEW
        assert not window.btn_start_scos.isChecked()
        assert window.btn_start_scos.text() == "Start SCOS"
        assert window.spn_window.isEnabled()          # parameters unlocked again

    @pytest.mark.parametrize("circle", [CIRCLE, HALF_OFF], ids=["inside", "half-off"])
    def test_a_measurable_roi_passes(self, window, warnings_shown, gain_reached, circle):
        window.image_widget.set_roi_circle(*circle)
        window.btn_start_scos.setChecked(True)
        assert warnings_shown == []
        assert gain_reached == [True]

    def test_full_frame_without_a_circle_passes(self, window, warnings_shown,
                                                gain_reached):
        window.image_widget._clear_roi()
        assert window._mask.all()
        window.btn_start_scos.setChecked(True)
        assert warnings_shown == []
        assert gain_reached == [True]


class TestCutImage:

    def test_refused_for_an_off_image_circle(self, window, cam):
        window.image_widget.set_roi_circle(*OFF_IMAGE)
        before = cam.applied_crop
        window._on_cut_requested(True)
        assert cam.roi_position is None             # nothing was asked of the camera
        assert cam.applied_crop == before
        assert not window.image_widget.is_cut
        assert "off the image" in window.status.currentMessage()

    def test_half_off_circle_still_cuts(self, window, cam):
        window.image_widget.set_roi_circle(*HALF_OFF)
        window._on_cut_requested(True)
        assert window.image_widget.is_cut
        assert window._mask.any()
