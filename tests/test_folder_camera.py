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
            lambda f: received.append(f.copy()),
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
            lambda f: received.append(f.copy()),
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
            lambda f: received.append(int(f[0, 0])),
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
        assert "sat_capacity" in info
        assert "model" in info

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
