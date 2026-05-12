"""Tests for core/recorder.py — HDF5Recorder."""
import math
from pathlib import Path

import h5py
import numpy as np
import pytest

from core.recorder import HDF5Recorder, FLUSH_EVERY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recorder(tmp_path: Path, **meta_overrides) -> HDF5Recorder:
    meta = {"frame_rate_hz": 20.0, "gain_db": 8.0, "window_size": 7}
    meta.update(meta_overrides)
    return HDF5Recorder(tmp_path / "scos_test.h5", meta)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_file_created(tmp_path):
    rec = _make_recorder(tmp_path)
    rec.close()
    assert (tmp_path / "scos_test.h5").exists()


def test_metadata_stored(tmp_path):
    rec = _make_recorder(tmp_path, gain_db=24.0, window_size=11)
    rec.close()
    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert f["metadata"].attrs["gain_db"] == pytest.approx(24.0)
        assert f["metadata"].attrs["window_size"] == 11


def test_round_trip_values(tmp_path):
    rec = _make_recorder(tmp_path)
    times   = [0.05 * i for i in range(10)]
    k2_raws = [0.1 + 0.01 * i for i in range(10)]
    k2_cors = [0.08 + 0.01 * i for i in range(10)]
    mean_is = [500.0 + i for i in range(10)]
    for t, r, c, m in zip(times, k2_raws, k2_cors, mean_is):
        rec.append(t, r, c, m)
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        np.testing.assert_allclose(f["time"][:],           times)
        np.testing.assert_allclose(f["k2_raw"][:],         k2_raws)
        np.testing.assert_allclose(f["k2_corr"][:],        k2_cors)
        np.testing.assert_allclose(f["bfi"][:],            [1/c for c in k2_cors])
        np.testing.assert_allclose(f["mean_intensity"][:], mean_is)


def test_non_positive_k2_corr_skipped(tmp_path):
    rec = _make_recorder(tmp_path)
    rec.append(0.0,  0.1, -0.1, 500.0)   # negative k2_corr — skip
    rec.append(0.05, 0.1,  0.0, 500.0)   # zero k2_corr — skip
    rec.append(0.1,  0.1,  0.1, 500.0)   # valid
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert len(f["time"]) == 1
        assert f["time"][0] == pytest.approx(0.1)


def test_flush_every_threshold(tmp_path):
    """Data is flushed automatically after FLUSH_EVERY appends."""
    rec = _make_recorder(tmp_path)
    for i in range(FLUSH_EVERY):
        rec.append(float(i), 0.1, 0.1, 500.0)
    # After FLUSH_EVERY appends the flush should have happened automatically
    assert rec.n_points == FLUSH_EVERY
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert len(f["time"]) == FLUSH_EVERY


def test_append_across_multiple_flushes(tmp_path):
    """Data written in two batches is concatenated correctly."""
    n = FLUSH_EVERY + 50
    rec = _make_recorder(tmp_path)
    for i in range(n):
        rec.append(float(i), 0.1, 0.1 + i * 1e-6, 500.0)
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert len(f["time"]) == n
        np.testing.assert_allclose(f["time"][:], np.arange(n, dtype=float))


def test_n_points_counter(tmp_path):
    rec = _make_recorder(tmp_path)
    for i in range(5):
        rec.append(float(i), 0.1, 0.1, 500.0)
    assert rec.n_points == 5
    rec.close()
    assert rec.n_points == 5


def test_path_property(tmp_path):
    rec = _make_recorder(tmp_path)
    assert rec.path == tmp_path / "scos_test.h5"
    rec.close()


def test_save_calibration(tmp_path):
    """Calibration arrays are stored in the 'calibration' group."""
    rec = _make_recorder(tmp_path)
    mean_dark  = np.full((10, 10), 42.0,  dtype=np.float64)
    var_dark   = np.full((10, 10), 0.5,   dtype=np.float64)
    var_bright = np.full((10, 10), 1.2,   dtype=np.float64)
    mask       = np.ones((10, 10), dtype=bool)
    rec.save_calibration(mean_dark, var_dark, var_bright, mask)
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert "calibration" in f
        np.testing.assert_allclose(f["calibration/mean_dark"][:],  42.0,  atol=1e-5)
        np.testing.assert_allclose(f["calibration/var_dark"][:],   0.5,   atol=1e-5)
        np.testing.assert_allclose(f["calibration/var_bright"][:], 1.2,   atol=1e-5)
        assert f["calibration/mask"].shape == (10, 10)


def test_save_calibration_none_arrays_skipped(tmp_path):
    """None arrays are silently skipped — no crash when calibration wasn't run."""
    rec = _make_recorder(tmp_path)
    rec.save_calibration(None, None, None, None)
    rec.close()

    with h5py.File(tmp_path / "scos_test.h5", "r") as f:
        assert "calibration" in f
        assert len(f["calibration"]) == 0   # empty group, no datasets
