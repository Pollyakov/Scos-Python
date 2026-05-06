"""
Tests for core/session.py — DarkCalCollector and BrightCalCollector.
"""

import numpy as np
import pytest
from scipy.ndimage import uniform_filter

from core.session import BrightCalCollector, DarkCalCollector, SessionConfig, State
from processor import SCOSProcessor, local_variance


# ---------------------------------------------------------------------------
# DarkCalCollector
# ---------------------------------------------------------------------------

def _make_frames(rng: np.random.Generator, n: int, h: int = 32, w: int = 32) -> np.ndarray:
    """Return an (n, h, w) uint16 array of random frames."""
    return rng.integers(0, 4096, size=(n, h, w), dtype=np.uint16)


def test_welford_mean_matches_numpy():
    rng    = np.random.default_rng(0)
    frames = _make_frames(rng, n=50)
    col    = DarkCalCollector(n_frames=50, window_size=1)

    for f in frames:
        col.add_frame(f)

    mean_got, _ = col.result()
    mean_ref    = frames.astype(np.float64).mean(axis=0)
    np.testing.assert_allclose(mean_got, mean_ref, rtol=1e-12)


def test_welford_variance_matches_numpy():
    rng    = np.random.default_rng(1)
    frames = _make_frames(rng, n=50)
    col    = DarkCalCollector(n_frames=50, window_size=1)

    for f in frames:
        col.add_frame(f)

    _, var_got = col.result()
    # window_size=1 → uniform_filter is a no-op → raw unbiased variance
    var_ref = frames.astype(np.float64).var(axis=0, ddof=1)
    np.testing.assert_allclose(var_got, var_ref, rtol=1e-12)


def test_welford_variance_spatial_filter_applied():
    """window_size > 1 should spatially smooth the variance."""
    rng    = np.random.default_rng(2)
    frames = _make_frames(rng, n=20)
    w      = 7
    col    = DarkCalCollector(n_frames=20, window_size=w)

    for f in frames:
        col.add_frame(f)

    _, var_got = col.result()
    raw_var = frames.astype(np.float64).var(axis=0, ddof=1)
    var_ref = uniform_filter(raw_var, size=w)
    np.testing.assert_allclose(var_got, var_ref, rtol=1e-12)


def test_done_flag():
    col = DarkCalCollector(n_frames=5, window_size=1)
    frame = np.ones((4, 4), dtype=np.uint16)
    assert not col.done
    for i in range(4):
        col.add_frame(frame)
        assert col.n_collected == i + 1
        assert not col.done
    col.add_frame(frame)
    assert col.done
    assert col.n_collected == 5


def test_result_raises_with_too_few_frames():
    col = DarkCalCollector(n_frames=10, window_size=1)
    col.add_frame(np.ones((4, 4), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        col.result()


# ---------------------------------------------------------------------------
# SessionConfig defaults
# ---------------------------------------------------------------------------

def test_session_config_defaults():
    cfg = SessionConfig()
    assert cfg.n_dark_frames   == 600
    assert cfg.n_bright_frames == 600
    assert cfg.window_size     == 7
    assert cfg.norm_seconds    == 5.0
    assert not cfg.save_frames


# ---------------------------------------------------------------------------
# State enum completeness
# ---------------------------------------------------------------------------

def test_state_enum_has_required_values():
    required = {"IDLE", "PREVIEW", "DARK_CAL", "BRIGHT_CAL",
                "MEASURING_INIT", "MEASURING", "FINISHED", "ERROR"}
    assert required.issubset({s.name for s in State})


# ---------------------------------------------------------------------------
# BrightCalCollector
# ---------------------------------------------------------------------------

def test_bright_mean_matches_numpy():
    rng    = np.random.default_rng(10)
    frames = _make_frames(rng, n=40)
    col    = BrightCalCollector(n_frames=40, window_size=1)

    for f in frames:
        col.add_frame(f)

    sp_im, _ = col.result(dark_mean=None)
    mean_ref  = frames.astype(np.float64).mean(axis=0)
    np.testing.assert_allclose(sp_im, mean_ref, rtol=1e-12)


def test_bright_sp_im_subtracts_dark_mean():
    rng    = np.random.default_rng(11)
    frames = _make_frames(rng, n=20)
    dark   = rng.integers(0, 100, size=frames.shape[1:], dtype=np.uint16).astype(np.float64)
    col    = BrightCalCollector(n_frames=20, window_size=1)

    for f in frames:
        col.add_frame(f)

    sp_im_with, _    = col.result(dark_mean=dark)
    sp_im_without, _ = col.result(dark_mean=None)
    np.testing.assert_allclose(sp_im_with, sp_im_without - dark, rtol=1e-12)


def test_bright_var_matches_calibrate_bright():
    """BrightCalCollector.result() must match processor.calibrate_bright exactly."""
    rng    = np.random.default_rng(12)
    frames = _make_frames(rng, n=30, h=32, w=32)
    dark   = rng.integers(0, 50, size=(32, 32), dtype=np.uint16).astype(np.float64)
    w      = 7

    # Online path
    col = BrightCalCollector(n_frames=30, window_size=w)
    for f in frames:
        col.add_frame(f)
    _, bright_var_online = col.result(dark_mean=dark)

    # Batch path: processor.calibrate_bright
    proc = SCOSProcessor(window_size=w)
    proc.dark_mean = dark
    proc.calibrate_bright(frames.transpose(1, 2, 0))  # (H, W, N)
    bright_var_batch = proc.bright_var

    np.testing.assert_allclose(bright_var_online, bright_var_batch, rtol=1e-12)


def test_bright_done_flag():
    col   = BrightCalCollector(n_frames=3, window_size=1)
    frame = np.ones((4, 4), dtype=np.uint16)
    assert not col.done
    col.add_frame(frame); assert col.n_collected == 1 and not col.done
    col.add_frame(frame); assert col.n_collected == 2 and not col.done
    col.add_frame(frame); assert col.done and col.n_collected == 3


def test_bright_result_raises_with_no_frames():
    col = BrightCalCollector(n_frames=10, window_size=1)
    with pytest.raises(RuntimeError):
        col.result()
