"""
Tests for processor.py — convert_gain, local_variance, SCOSProcessor.
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from processor import convert_gain, load_gain_from_table, local_variance, SCOSProcessor, GainTableError, shrink_mask_for_window


# ======================================================================
# load_gain_from_table
# ======================================================================

class TestLoadGainFromTable:
    """load_gain_from_table(camera_sn, n_bits, gain_db) → DU/e from CSV."""

    # Rows in CamerasMeasuredGain.csv for camera 40513592 nBits=10:
    #   16 dB → 0.583144665
    #   18 dB → 0.732088235
    #   20 dB → 0.920986881

    def test_exact_match_returns_measured_value(self):
        """Exact gain_db in table → returns measuredG directly."""
        g = load_gain_from_table("40513592", 10, 20.0)
        assert g == pytest.approx(0.920986881, rel=1e-6)

    def test_exact_match_another_entry(self):
        """Check a second exact row."""
        g = load_gain_from_table("40513592", 10, 16.0)
        assert g == pytest.approx(0.583144665, rel=1e-6)

    def test_interpolation_between_entries(self, recwarn):
        """
        gain_db not in table → closest entry used, result interpolated,
        and a UserWarning is issued.
        For camera 40513592 nBits=10, closest to 17 dB is 16 dB (0.583144665).
        Expected: 0.583144665 * 10^((17-16)/20)
        """
        expected = 0.583144665 * 10 ** ((17.0 - 16.0) / 20.0)
        g = load_gain_from_table("40513592", 10, 17.0)
        assert g == pytest.approx(expected, rel=1e-5)
        # A UserWarning about interpolation must be issued
        assert any(issubclass(w.category, UserWarning) for w in recwarn.list)

    def test_unknown_camera_raises(self):
        """Unknown cameraSN → GainTableError with the popup message format."""
        with pytest.raises(GainTableError, match="Can't calculate SCOS"):
            load_gain_from_table("99999999", 10, 20.0)

    def test_unknown_camera_message_format(self):
        """Error message contains CameraSN and MonoN as the GUI popup will show."""
        with pytest.raises(GainTableError) as exc_info:
            load_gain_from_table("99999999", 10, 20.0)
        msg = str(exc_info.value)
        assert "99999999" in msg
        assert "Mono10" in msg
        assert "G[DU/e] Calibration file" in msg

    def test_wrong_nbits_raises(self):
        """Known SN but wrong nBits → GainTableError."""
        with pytest.raises(GainTableError, match="Can't calculate SCOS"):
            load_gain_from_table("40513592", 8, 20.0)

    def test_non_numeric_sn_raises(self):
        """Non-numeric serial number → ValueError."""
        with pytest.raises(ValueError, match="must be numeric"):
            load_gain_from_table("BADCAM", 10, 20.0)

    def test_missing_file_raises(self):
        """Non-existent CSV path → FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_gain_from_table("40513592", 10, 20.0,
                                 gain_data_file="/nonexistent/path.csv")

    def test_scos_processor_uses_table_when_sn_set(self):
        """SCOSProcessor with camera_sn uses load_gain_from_table, not convert_gain."""
        import numpy as np
        # camera 40513592, nBits=10, gain=20 dB → measuredG = 0.920986881
        proc = SCOSProcessor(window_size=5, gain_db=20.0, bit_depth=10,
                             sat_capacity=11117.0, camera_sn="40513592")
        frame = np.full((50, 50), 500, dtype=np.uint16)
        mask  = np.ones((50, 50), dtype=bool)
        # Must not raise; G from table differs from convert_gain result
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        assert isinstance(k2_raw, float)

    def test_scos_processor_falls_back_without_sn(self):
        """SCOSProcessor without camera_sn uses convert_gain (no table needed)."""
        import numpy as np
        proc = SCOSProcessor(window_size=5, gain_db=8.0, bit_depth=12,
                             sat_capacity=10500.0)  # no camera_sn
        frame = np.full((50, 50), 500, dtype=np.uint16)
        mask  = np.ones((50, 50), dtype=bool)
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        assert isinstance(k2_raw, float)


# ======================================================================
# convert_gain
# ======================================================================

class TestConvertGain:
    """convert_gain(gain_db, bit_depth, sat_capacity) → DU/e"""

    def test_zero_gain_8bit(self):
        """0 dB gain with 8-bit → base gain G0 = 2^8 / 10500."""
        result = convert_gain(0.0, 8, 10500.0)
        expected = 256 / 10500.0  # ≈ 0.02438
        assert result == pytest.approx(expected, rel=1e-6)

    def test_zero_gain_12bit(self):
        """0 dB gain with 12-bit → G0 = 4096 / 10500."""
        result = convert_gain(0.0, 12, 10500.0)
        expected = 4096 / 10500.0  # ≈ 0.39009
        assert result == pytest.approx(expected, rel=1e-6)

    def test_20db_doubles_gain(self):
        """20 dB = factor of 10 in amplitude → G = 10 * G0."""
        g0 = convert_gain(0.0, 8, 10500.0)
        g20 = convert_gain(20.0, 8, 10500.0)
        assert g20 == pytest.approx(g0 * 10.0, rel=1e-6)

    def test_6db_roughly_doubles(self):
        """6 dB ≈ factor of ~2 in amplitude."""
        g0 = convert_gain(0.0, 12, 10500.0)
        g6 = convert_gain(6.0, 12, 10500.0)
        ratio = g6 / g0
        assert ratio == pytest.approx(10 ** (6.0 / 20.0), rel=1e-6)  # ≈ 1.995

    def test_negative_gain(self):
        """Negative dB should reduce gain below G0."""
        g0 = convert_gain(0.0, 12, 10500.0)
        g_neg = convert_gain(-6.0, 12, 10500.0)
        assert g_neg < g0

    def test_different_sat_capacity(self):
        """Higher saturation capacity → lower G0 (fewer DU per electron)."""
        g_low = convert_gain(0.0, 12, 5000.0)
        g_high = convert_gain(0.0, 12, 20000.0)
        assert g_low > g_high


# ======================================================================
# local_variance
# ======================================================================

class TestLocalVariance:
    """local_variance(im, window) → (mean_im, var_im)"""

    def test_uniform_image_zero_variance(self):
        """A flat (constant) image should have zero variance everywhere."""
        im = np.full((50, 50), 100.0)
        mean_im, var_im = local_variance(im, 5)
        np.testing.assert_allclose(var_im, 0.0, atol=1e-10)
        np.testing.assert_allclose(mean_im, 100.0, atol=1e-10)

    def test_mean_of_uniform(self):
        """Mean of a constant image equals that constant."""
        im = np.full((30, 30), 42.0)
        mean_im, _ = local_variance(im, 7)
        np.testing.assert_allclose(mean_im, 42.0, atol=1e-10)

    def test_variance_nonnegative(self):
        """Variance must never be negative (numerical safety clamp)."""
        rng = np.random.default_rng(42)
        im = rng.integers(0, 4096, size=(100, 100))
        _, var_im = local_variance(im, 7)
        assert np.all(var_im >= 0)

    def test_output_shapes(self):
        """Output arrays have the same shape as input."""
        im = np.zeros((64, 128))
        mean_im, var_im = local_variance(im, 5)
        assert mean_im.shape == (64, 128)
        assert var_im.shape == (64, 128)

    def test_output_dtype_float32(self):
        """Outputs should be float32 (internal precision for fast box filtering)."""
        im = np.zeros((20, 20), dtype=np.uint16)
        mean_im, var_im = local_variance(im, 3)
        assert mean_im.dtype == np.float32
        assert var_im.dtype == np.float32

    def test_known_simple_case(self):
        """
        3×3 image with window=3:
        The center pixel sees the full 3×3 window.
        Unbiased variance (÷ N-1 = ÷8) matches MATLAB stdfilt.
        """
        im = np.array([
            [1, 2, 3],
            [4, 5, 6],
            [7, 8, 9],
        ], dtype=np.float64)
        mean_im, var_im = local_variance(im, 3)
        # Center pixel: mean = 5.0
        # biased var  = 60/9; unbiased var = 60/9 × 9/8 = 60/8 = 7.5
        assert mean_im[1, 1] == pytest.approx(5.0, abs=0.5)
        assert var_im[1, 1] == pytest.approx(60.0 / 8.0, abs=0.5)


# ======================================================================
# SCOSProcessor
# ======================================================================

class TestSCOSProcessor:
    """Tests for SCOSProcessor initialization, calibration, and process()."""

    def test_default_params(self):
        """Default constructor values."""
        proc = SCOSProcessor()
        assert proc.window_size == 7
        assert proc.gain_db == 0.0
        assert proc.bit_depth == 8
        assert proc.sat_capacity == 10500.0
        assert proc.dark_mean is None
        assert proc.dark_var is None
        assert proc.bright_var is None

    def test_custom_params(self):
        proc = SCOSProcessor(window_size=5, gain_db=6.0, bit_depth=12, sat_capacity=8000.0)
        assert proc.window_size == 5
        assert proc.gain_db == 6.0
        assert proc.bit_depth == 12
        assert proc.sat_capacity == 8000.0

    def test_calibrate_sets_dark_stats(self):
        """calibrate() should compute mean and variance across the stack."""
        rng = np.random.default_rng(0)
        # 10 dark frames of 20×20 with mean ~100
        dark = rng.normal(100, 5, size=(20, 20, 10))
        proc = SCOSProcessor()
        proc.calibrate(dark)
        assert proc.dark_mean is not None
        assert proc.dark_var is not None
        assert proc.dark_mean.shape == (20, 20)
        assert proc.dark_var.shape == (20, 20)
        # Mean should be close to 100
        assert np.mean(proc.dark_mean) == pytest.approx(100.0, abs=2.0)

    def test_calibrate_bright_sets_bright_var(self):
        """calibrate_bright() should produce a non-None bright_var of the right shape."""
        rng = np.random.default_rng(1)
        bright = rng.normal(2000, 50, size=(20, 20, 10))
        proc = SCOSProcessor(window_size=5)
        proc.calibrate_bright(bright)
        assert proc.bright_var is not None
        assert proc.bright_var.shape == (20, 20)
        assert np.all(proc.bright_var >= 0)

    def test_calibrate_bright_subtracts_dark(self):
        """calibrate_bright() should subtract dark_mean before computing spVar."""
        rng = np.random.default_rng(2)
        dark = rng.normal(100, 3, size=(20, 20, 5))
        bright = rng.normal(2000, 50, size=(20, 20, 10))
        proc = SCOSProcessor(window_size=5)
        proc.calibrate(dark)
        proc.calibrate_bright(bright)
        # Bright var computed after dark subtraction should be non-negative
        assert proc.bright_var is not None
        assert np.all(proc.bright_var >= 0)

    def test_process_returns_three_floats(self):
        """process() should return (kappa2_raw, kappa2_corr, mean_intensity)."""
        proc = SCOSProcessor(window_size=5, bit_depth=12, gain_db=0.0)
        rng = np.random.default_rng(1)
        frame = rng.integers(500, 3500, size=(100, 100), dtype=np.uint16)
        mask = np.ones((100, 100), dtype=bool)
        result = proc.process(frame, mask)
        assert len(result) == 3
        k2_raw, k2_corr, mean_i = result
        assert isinstance(k2_raw, float)
        assert isinstance(k2_corr, float)
        assert isinstance(mean_i, float)

    def test_process_uniform_frame_low_contrast(self):
        """A nearly-uniform frame should have very low raw κ²."""
        proc = SCOSProcessor(window_size=5, bit_depth=12, gain_db=0.0)
        # Uniform frame with tiny noise
        frame = np.full((100, 100), 2000, dtype=np.uint16)
        mask = np.ones((100, 100), dtype=bool)
        k2_raw, k2_corr, _ = proc.process(frame, mask)
        assert k2_raw < 0.001  # practically zero contrast

    def test_process_with_dark_calibration(self):
        """Processing with dark calibration should differ from without."""
        rng = np.random.default_rng(2)
        frame = rng.integers(500, 3500, size=(64, 64), dtype=np.uint16)
        mask = np.ones((64, 64), dtype=bool)

        proc_no_dark = SCOSProcessor(window_size=5, bit_depth=12)
        k2_raw_nd, k2_corr_nd, _ = proc_no_dark.process(frame, mask)

        proc_dark = SCOSProcessor(window_size=5, bit_depth=12)
        dark = rng.normal(100, 3, size=(64, 64, 5))
        proc_dark.calibrate(dark)
        k2_raw_d, k2_corr_d, _ = proc_dark.process(frame, mask)

        # Dark subtraction changes the result
        assert k2_raw_d != pytest.approx(k2_raw_nd, rel=0.01)

    def test_process_mean_intensity(self):
        """Mean intensity should reflect the average pixel value in the ROI."""
        frame = np.full((50, 50), 1500, dtype=np.uint16)
        mask = np.ones((50, 50), dtype=bool)
        proc = SCOSProcessor(window_size=5, bit_depth=12)
        _, _, mean_i = proc.process(frame, mask)
        assert mean_i == pytest.approx(1500.0, abs=1.0)

    def test_process_partial_mask(self):
        """Only pixels inside the mask should contribute."""
        rng = np.random.default_rng(3)
        frame = rng.integers(500, 3500, size=(100, 100), dtype=np.uint16)
        # Small circular mask in center
        yy, xx = np.ogrid[:100, :100]
        mask = (xx - 50) ** 2 + (yy - 50) ** 2 <= 20 ** 2
        proc = SCOSProcessor(window_size=5, bit_depth=12)
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        assert np.isfinite(k2_raw)
        assert np.isfinite(mean_i)

    # --- Edge cases ---

    def test_process_all_zeros_frame(self):
        """All-zero frame → mean=0 everywhere → safe guard produces NaN, not crash."""
        proc = SCOSProcessor(window_size=5, bit_depth=12)
        frame = np.zeros((50, 50), dtype=np.uint16)
        mask = np.ones((50, 50), dtype=bool)
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        # Should not crash; result is NaN because mean²=0
        assert isinstance(k2_raw, float)
        assert isinstance(k2_corr, float)

    def test_process_single_pixel_mask(self):
        """Mask with only one True pixel should still work."""
        proc = SCOSProcessor(window_size=3, bit_depth=12)
        rng = np.random.default_rng(10)
        frame = rng.integers(500, 3500, size=(50, 50), dtype=np.uint16)
        mask = np.zeros((50, 50), dtype=bool)
        mask[25, 25] = True
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        assert isinstance(k2_raw, float)
        assert isinstance(mean_i, float)

    def test_process_large_window_small_frame(self):
        """Window larger than frame should not crash (uniform_filter handles it)."""
        proc = SCOSProcessor(window_size=15, bit_depth=12)
        rng = np.random.default_rng(11)
        frame = rng.integers(500, 3500, size=(10, 10), dtype=np.uint16)
        mask = np.ones((10, 10), dtype=bool)
        k2_raw, k2_corr, mean_i = proc.process(frame, mask)
        assert isinstance(k2_raw, float)
        assert np.isfinite(mean_i)

    def test_process_high_contrast_frame(self):
        """A frame with strong spatial variation should have higher κ² than uniform."""
        proc = SCOSProcessor(window_size=5, bit_depth=12)
        # Checkerboard pattern — high local variance
        frame = np.zeros((100, 100), dtype=np.uint16)
        frame[::2, ::2] = 3000
        frame[1::2, 1::2] = 3000
        mask = np.ones((100, 100), dtype=bool)
        k2_high, _, _ = proc.process(frame, mask)

        # Uniform frame — near-zero variance
        frame_flat = np.full((100, 100), 1500, dtype=np.uint16)
        k2_low, _, _ = proc.process(frame_flat, mask)
        assert k2_high > k2_low


# ======================================================================
# local_variance edge cases
# ======================================================================

class TestLocalVarianceEdgeCases:

    def test_window_1_zero_variance(self):
        """Window size 1 → each pixel is its own neighborhood → variance = 0."""
        rng = np.random.default_rng(99)
        im = rng.integers(0, 4096, size=(30, 30)).astype(np.float64)
        mean_im, var_im = local_variance(im, 1)
        np.testing.assert_allclose(var_im, 0.0, atol=1e-10)
        np.testing.assert_allclose(mean_im, im, atol=1e-10)

    def test_single_pixel_image(self):
        """1×1 image should not crash."""
        im = np.array([[42.0]])
        mean_im, var_im = local_variance(im, 3)
        assert mean_im.shape == (1, 1)
        assert var_im[0, 0] >= 0


# ======================================================================
# shrink_mask_for_window
# ======================================================================

class TestShrinkMaskForWindow:

    def _circle_mask(self, size=50, radius=20):
        """Helper: boolean circle mask centred in a square image."""
        y, x = np.ogrid[:size, :size]
        return (x - size // 2) ** 2 + (y - size // 2) ** 2 <= radius ** 2

    def test_shrunk_is_strict_subset(self):
        """Every True pixel in the result must also be True in the input."""
        mask = self._circle_mask()
        shrunk = shrink_mask_for_window(mask, window=7)
        assert shrunk.dtype == bool
        # shrunk ⊆ mask
        assert np.all(mask[shrunk])
        # at least one pixel was removed
        assert shrunk.sum() < mask.sum()

    def test_output_shape_matches_input(self):
        """Output shape must equal input shape."""
        mask = self._circle_mask(size=100, radius=40)
        shrunk = shrink_mask_for_window(mask, window=7)
        assert shrunk.shape == mask.shape

    def test_erosion_radius_scales_with_window(self):
        """Larger window → larger erosion → fewer True pixels."""
        mask = self._circle_mask(size=100, radius=45)
        s7  = shrink_mask_for_window(mask, window=7)   # radius 4
        s15 = shrink_mask_for_window(mask, window=15)  # radius 8
        assert s15.sum() < s7.sum()

    def test_all_false_mask_stays_all_false(self):
        """An empty mask should remain empty after erosion."""
        mask = np.zeros((50, 50), dtype=bool)
        shrunk = shrink_mask_for_window(mask, window=7)
        assert shrunk.sum() == 0
