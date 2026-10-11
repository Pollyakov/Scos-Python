"""
Tests for FolderMockCamera.
No real camera or Basler SDK needed.
"""

import sys
import os
import re
import numpy as np
import pytest
import tifffile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import Qt, QCoreApplication
from folder_camera import FolderMockCamera, find_dark_dir

_app = QCoreApplication.instance() or QCoreApplication(sys.argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tiffs(folder, n: int = 5, h: int = 16, w: int = 16) -> list:
    """Write n individual per-frame TIFF files in Basler naming convention."""
    paths = []
    rng = np.random.default_rng(0)
    for i in range(n):
        frame = rng.integers(100, 4096, size=(h, w), dtype=np.uint16)
        name  = f"Basler_a2A1920-160umPRO__40513592__20241101_163604036_{i:04d}.tiff"
        path  = folder / name
        tifffile.imwrite(str(path), frame)
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# Emission tests
# ---------------------------------------------------------------------------

class TestFolderCameraEmission:

    def test_emits_correct_number_of_frames(self, tmp_path):
        """With loop=False, frame_ready fires exactly N times then stops."""
        rec_dir = tmp_path / "recording"
        rec_dir.mkdir()
        _write_tiffs(rec_dir, n=5)

        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000   # no sleeping

        received = []
        cam.frame_ready.connect(
            lambda f, t: received.append(f.copy()),
            Qt.ConnectionType.DirectConnection,
        )
        cam.start_capture()
        cam.wait()

        assert len(received) == 5

    def test_emitted_frame_shape_and_dtype(self, tmp_path):
        """Each emitted frame matches the TIFF's shape and dtype."""
        rec_dir = tmp_path / "recording"
        rec_dir.mkdir()
        _write_tiffs(rec_dir, n=3, h=8, w=12)

        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000

        received = []
        cam.frame_ready.connect(
            lambda f, t: received.append(f.copy()),
            Qt.ConnectionType.DirectConnection,
        )
        cam.start_capture()
        cam.wait()

        assert len(received) == 3
        for frame in received:
            assert frame.shape == (8, 12)
            assert frame.dtype == np.uint16

    def test_frames_sorted_by_index(self, tmp_path):
        """Frames are emitted in ascending index order, not filesystem order."""
        rec_dir = tmp_path / "recording"
        rec_dir.mkdir()
        # Write files with a non-trivial naming; sort by trailing _NNNN index
        for i in [3, 1, 0, 2, 4]:
            val = np.full((4, 4), i, dtype=np.uint16)
            name = f"Basler_cam__123__20240101_000000000_{i:04d}.tiff"
            tifffile.imwrite(str(rec_dir / name), val)

        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000
        received = []
        cam.frame_ready.connect(
            lambda f, t: received.append(int(f[0, 0])),
            Qt.ConnectionType.DirectConnection,
        )
        cam.start_capture()
        cam.wait()

        assert received == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Dark folder auto-detection
# ---------------------------------------------------------------------------

class TestDarkDirDetection:

    def test_finds_flat_dark_folder(self, tmp_path):
        rec   = tmp_path / "exp005"
        dark  = tmp_path / "exp005_dark"
        rec.mkdir(); dark.mkdir()
        assert find_dark_dir(rec) == dark

    def test_handles_double_nesting(self, tmp_path):
        """Pylon double-nesting: outer/outer/TIFFs."""
        rec    = tmp_path / "exp005"
        outer  = tmp_path / "exp005_dark"
        inner  = outer / "exp005_dark"
        rec.mkdir(); outer.mkdir(); inner.mkdir()
        assert find_dark_dir(rec) == inner

    def test_returns_none_when_no_dark_folder(self, tmp_path):
        rec = tmp_path / "exp005"
        rec.mkdir()
        assert find_dark_dir(rec) is None


# ---------------------------------------------------------------------------
# Setters and get_info
# ---------------------------------------------------------------------------

class TestFolderCameraAPI:

    def test_setters_store_values(self, tmp_path):
        rec = tmp_path / "rec"; rec.mkdir()
        _write_tiffs(rec, n=2)
        cam = FolderMockCamera(str(rec))
        cam.set_exposure(8000.0)
        cam.set_gain(12.0)
        cam.set_frame_rate(30.0)
        cam.set_pixel_format("Mono12")
        cam.set_trigger(True, 50.0)
        cam.set_roi(0, 0, 100, 100)

        assert cam.exposure_us   == 8000.0
        assert cam.gain_db       == 12.0
        assert cam.frame_rate    == 30.0
        assert cam.pixel_format  == "Mono12"
        assert cam.trigger_mode  == "On"
        assert cam.trigger_delay == 50.0
        assert cam.roi_position  == (0, 0, 100, 100)

    def test_get_info_after_open(self, tmp_path):
        rec = tmp_path / "rec"; rec.mkdir()
        _write_tiffs(rec, n=2, h=8, w=12)
        cam = FolderMockCamera(str(rec))
        cam.open()
        info = cam.get_info()
        assert info["width"]  == 12
        assert info["height"] == 8
        assert "model" in info
        # The serial is what the gain lookup needs; saturation capacity is
        # deliberately absent (supervisor's instruction, 2026-09-23).
        assert "serial" in info
        assert "sat_capacity" not in info

    def test_get_calibration_mat_none_when_absent(self, tmp_path):
        rec = tmp_path / "rec"; rec.mkdir()
        _write_tiffs(rec, n=2)
        cam = FolderMockCamera(str(rec))
        assert cam.get_calibration_mat() is None

    def test_get_mask_mat_none_when_absent(self, tmp_path):
        rec = tmp_path / "rec"; rec.mkdir()
        _write_tiffs(rec, n=2)
        cam = FolderMockCamera(str(rec))
        assert cam.get_mask_mat() is None


# ---------------------------------------------------------------------------
# "Cut Image" crops the frames, as the camera would (todo U6, plan decision D4)
# ---------------------------------------------------------------------------

from core.crop import Crop


def _play(cam, contiguous: list | None = None) -> list:
    received = []

    def _on_frame(f, t):
        if contiguous is not None:
            # Checked on the emitted array itself — the copy kept below is
            # always contiguous, so checking that would prove nothing.
            contiguous.append(f.flags["C_CONTIGUOUS"])
        received.append(f.copy())

    cam.frame_ready.connect(_on_frame, Qt.ConnectionType.DirectConnection)
    cam.start_capture()
    cam.wait()
    return received


class TestFolderCameraCrop:

    def _recording(self, tmp_path, n=3, h=20, w=30):
        rec_dir = tmp_path / "recording"
        rec_dir.mkdir()
        paths = _write_tiffs(rec_dir, n=n, h=h, w=w)
        return rec_dir, [tifffile.imread(str(p)) for p in paths]

    def test_full_sensor_until_cut(self, tmp_path):
        rec_dir, _ = self._recording(tmp_path)
        cam = FolderMockCamera(str(rec_dir), loop=False)
        assert cam.applied_crop is None            # not open yet
        cam.open()
        assert cam.sensor_size() == (30, 20)
        assert cam.applied_crop == Crop(0, 0, 30, 20, 30, 20)

    def test_cut_frames_are_the_recording_cut_out(self, tmp_path):
        rec_dir, originals = self._recording(tmp_path)
        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000
        cam.open()
        assert cam.set_roi(4, 3, 10, 8) == Crop(4, 3, 10, 8, 30, 20)
        contiguous = []
        received = _play(cam, contiguous)
        assert len(received) == 3
        for got, full in zip(received, originals):
            np.testing.assert_array_equal(got, full[3:11, 4:14])
        # A contiguous array of its own, like a camera buffer — not a view
        # that keeps the whole full-size frame alive.
        assert contiguous == [True, True, True]

    def test_dark_folder_is_cut_the_same_way(self, tmp_path):
        # The dark calibration must be built from frames of the same size and
        # position as the measurement's.
        rec_dir, _ = self._recording(tmp_path)
        dark_dir = tmp_path / "recording_dark"
        dark_dir.mkdir()
        darks = [tifffile.imread(str(p)) for p in _write_tiffs(dark_dir, n=2, h=20, w=30)]
        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000
        cam.open()
        cam.set_roi(4, 3, 10, 8)
        assert cam.set_playback_source("dark")
        received = _play(cam)
        assert len(received) == 2
        for got, full in zip(received, darks):
            np.testing.assert_array_equal(got, full[3:11, 4:14])

    def test_full_image_restores_whole_frames(self, tmp_path):
        rec_dir, originals = self._recording(tmp_path)
        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.frame_rate = 1000
        cam.open()
        cam.set_roi(4, 3, 10, 8)
        assert cam.clear_roi() == Crop(0, 0, 30, 20, 30, 20)
        for got, full in zip(_play(cam), originals):
            np.testing.assert_array_equal(got, full)

    def test_a_crop_outside_the_frames_is_refused(self, tmp_path):
        rec_dir, _ = self._recording(tmp_path)
        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.open()
        warnings = []
        cam.warning.connect(warnings.append, Qt.ConnectionType.DirectConnection)
        applied = cam.set_roi(25, 0, 10, 8)          # 25 + 10 > 30
        assert applied.is_full_sensor and cam.roi_position is None
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Dark calibration at the maximum rate (todo U11)
# ---------------------------------------------------------------------------

class TestFolderCameraMaxFrameRate:

    def _cam(self, tmp_path, n):
        rec_dir = tmp_path / "recording"
        rec_dir.mkdir()
        _write_tiffs(rec_dir, n=n)
        cam = FolderMockCamera(str(rec_dir), loop=False)
        cam.open()
        cam.set_frame_rate(20.0)
        cam.set_exposure(5000.0)
        return cam

    def test_maximum_is_one_over_the_exposure_and_off_goes_back(self, tmp_path):
        cam = self._cam(tmp_path, 2)
        assert cam.set_max_frame_rate(True) == pytest.approx(200.0)
        assert cam.set_max_frame_rate(False) == 20.0

    def test_playback_really_runs_faster(self, tmp_path):
        # 6 frames at 20 Hz take >= 0.25 s; at 200 fps far less.
        cam = self._cam(tmp_path, 6)
        cam.set_max_frame_rate(True)
        stamps = []
        cam.frame_ready.connect(lambda f, t: stamps.append(t),
                                Qt.ConnectionType.DirectConnection)
        cam.start_capture()
        cam.wait()
        assert len(stamps) == 6
        assert stamps[-1] - stamps[0] < 0.2

    def test_at_the_set_rate_it_stays_slow(self, tmp_path):
        # The control for the test above: same frames, limit on.
        cam = self._cam(tmp_path, 6)
        stamps = []
        cam.frame_ready.connect(lambda f, t: stamps.append(t),
                                Qt.ConnectionType.DirectConnection)
        cam.start_capture()
        cam.wait()
        assert stamps[-1] - stamps[0] >= 0.2
