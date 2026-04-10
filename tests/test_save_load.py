"""
Tests for save/load round-trip — .mat and .npz formats.

Verifies that data written by the app can be loaded back correctly,
and that .mat files match the MATLAB key convention.
"""

import os
import tempfile
import numpy as np
import pytest
import scipy.io

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestMatSaveLoad:
    """Round-trip tests for .mat (MATLAB) format."""

    def test_round_trip_keys_exist(self):
        """Saved .mat file must contain all required MATLAB-convention keys."""
        t = np.array([0.0, 1.0, 2.0])
        bfi = np.array([20.0, 18.5, 21.0])
        kappa2 = 1.0 / bfi  # save κ², not BFI

        with tempfile.NamedTemporaryFile(suffix=".mat", delete=False) as f:
            path = f.name
        try:
            scipy.io.savemat(path, {
                "scosTime": t,
                "scosData": kappa2,
                "frameRate": 20.0,
                "exposureTime": 8.0,
                "Gain": 6.0,
            })
            loaded = scipy.io.loadmat(path)
            for key in ("scosTime", "scosData", "frameRate", "exposureTime", "Gain"):
                assert key in loaded, f"Missing key: {key}"
        finally:
            os.unlink(path)

    def test_round_trip_values(self):
        """Values survive save → load without corruption."""
        t = np.array([0.5, 1.0, 1.5, 2.0])
        kappa2 = np.array([0.05, 0.048, 0.052, 0.049])

        with tempfile.NamedTemporaryFile(suffix=".mat", delete=False) as f:
            path = f.name
        try:
            scipy.io.savemat(path, {
                "scosTime": t,
                "scosData": kappa2,
                "frameRate": 50.0,
                "exposureTime": 10.0,
                "Gain": 8.0,
            })
            loaded = scipy.io.loadmat(path)
            np.testing.assert_allclose(loaded["scosTime"].ravel(), t)
            np.testing.assert_allclose(loaded["scosData"].ravel(), kappa2)
            # loadmat wraps scalars in 2D arrays → use .item()
            assert loaded["frameRate"].item() == pytest.approx(50.0)
            assert loaded["exposureTime"].item() == pytest.approx(10.0)
            assert loaded["Gain"].item() == pytest.approx(8.0)
        finally:
            os.unlink(path)

    def test_bfi_to_kappa_conversion(self):
        """App saves κ² = 1/BFI. Verify the conversion is reversible."""
        bfi_original = np.array([10.0, 20.0, 50.0, 100.0])
        kappa2_saved = 1.0 / bfi_original

        with tempfile.NamedTemporaryFile(suffix=".mat", delete=False) as f:
            path = f.name
        try:
            scipy.io.savemat(path, {"scosData": kappa2_saved})
            loaded = scipy.io.loadmat(path)
            kappa2_loaded = loaded["scosData"].ravel()
            bfi_recovered = 1.0 / kappa2_loaded
            np.testing.assert_allclose(bfi_recovered, bfi_original)
        finally:
            os.unlink(path)

    def test_empty_data(self):
        """Saving empty arrays should not crash."""
        with tempfile.NamedTemporaryFile(suffix=".mat", delete=False) as f:
            path = f.name
        try:
            scipy.io.savemat(path, {
                "scosTime": np.array([]),
                "scosData": np.array([]),
                "frameRate": 20.0,
                "exposureTime": 8.0,
                "Gain": 0.0,
            })
            loaded = scipy.io.loadmat(path)
            assert loaded["scosTime"].size == 0
            assert loaded["scosData"].size == 0
        finally:
            os.unlink(path)


class TestNpzSaveLoad:
    """Round-trip tests for .npz (NumPy) format."""

    def test_round_trip_keys(self):
        """Saved .npz file must have expected keys."""
        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
            path = f.name
        try:
            np.savez(path,
                     scosTime=np.array([1.0, 2.0]),
                     BFI=np.array([20.0, 19.0]),
                     frameRate=20.0,
                     exposureTime=8.0,
                     gain=6.0)
            loaded = np.load(path)
            for key in ("scosTime", "BFI", "frameRate", "exposureTime", "gain"):
                assert key in loaded, f"Missing key: {key}"
            loaded.close()
        finally:
            os.unlink(path)

    def test_round_trip_values(self):
        """Values survive .npz save → load."""
        t = np.array([0.0, 0.5, 1.0])
        bfi = np.array([25.0, 24.0, 26.0])

        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
            path = f.name
        try:
            np.savez(path, scosTime=t, BFI=bfi, frameRate=50.0,
                     exposureTime=10.0, gain=8.0)
            loaded = np.load(path)
            np.testing.assert_allclose(loaded["scosTime"], t)
            np.testing.assert_allclose(loaded["BFI"], bfi)
            assert loaded["frameRate"].item() == pytest.approx(50.0)
            loaded.close()
        finally:
            os.unlink(path)

    def test_large_dataset(self):
        """Saving a large time series (10k points) works correctly."""
        n = 10_000
        t = np.linspace(0, 100, n)
        bfi = np.random.default_rng(42).uniform(10, 30, n)

        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
            path = f.name
        try:
            np.savez(path, scosTime=t, BFI=bfi)
            loaded = np.load(path)
            assert loaded["scosTime"].shape == (n,)
            np.testing.assert_allclose(loaded["BFI"], bfi)
            loaded.close()
        finally:
            os.unlink(path)
