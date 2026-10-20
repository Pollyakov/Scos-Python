"""
Tests for core/disk_space.py — the free-space estimate and limits for saving
raw frames (docs/Plan_Frame_Saving.md, decisions 3 and 4).
"""

import math
import shutil

import pytest

from core import disk_space as ds

FULL = (1936, 1216)
CUT = (672, 667)


class TestFrameBytes:
    def test_full_frame_mono12(self):
        # Measured 2026-10-20: one full uncompressed TIFF = 4.71 MB.
        assert ds.frame_bytes(*FULL, 2) == pytest.approx(4.71e6, rel=0.01)
        assert ds.frame_bytes(*FULL, 2) >= 4_715_794      # never below the measured file

    def test_mono8_is_half_the_pixels(self):
        px = FULL[0] * FULL[1]
        assert ds.frame_bytes(*FULL, 1) - px == ds.frame_bytes(*FULL, 2) - 2 * px


class TestEstimate:
    def test_counts_every_phase(self):
        e = ds.estimate(*CUT, 2, frame_rate_hz=20, duration_s=60, n_dark=600, n_bright=600)
        assert e.n_frames == 600 + 600 + 1200
        assert e.bytes_needed == e.n_frames * ds.frame_bytes(*CUT, 2)

    def test_margin(self):
        e = ds.estimate(*CUT, 2, 20, 60, 600, 600)
        assert e.bytes_required_free == math.ceil(e.bytes_needed * 1.2) + 5 * ds.GB

    def test_one_hour_full_frame_is_about_340_gb(self):
        e = ds.estimate(*FULL, 2, 20, 3600, 0, 0)
        assert e.bytes_needed / ds.GB == pytest.approx(340, rel=0.02)

    @pytest.mark.parametrize("unlimited", [0.0, math.inf])
    def test_unlimited_is_four_hours(self, unlimited):
        e = ds.estimate(*CUT, 2, 20, unlimited, 600, 600)
        assert e.duration_s == 4 * 3600
        assert e.n_frames == 1200 + 20 * 4 * 3600

    def test_fractional_rate_rounds_up(self):
        assert ds.estimate(*CUT, 2, 20.5, 1, 0, 0).n_frames == 21


class TestLimits:
    def test_constants(self):
        # The values proposed to Vika on 2026-10-07 and chosen on 2026-10-20.
        assert ds.STOP_BELOW_BYTES == 2 * ds.GB
        assert ds.START_MARGIN_BYTES == 5 * ds.GB
        assert ds.START_MARGIN_FACTOR == 1.2
        assert ds.CHECK_INTERVAL_S == 30

    def test_free_bytes(self, tmp_path):
        assert ds.free_bytes(tmp_path) == shutil.disk_usage(tmp_path).free

    def test_minutes_that_fit(self):
        # 170 GB free (the rig PC, 2026-10-20), full frame at 20 Hz: ≈ 30 min.
        assert ds.minutes_that_fit(170 * ds.GB, *FULL, 2, 20) == pytest.approx(25, abs=2)
        assert ds.minutes_that_fit(1 * ds.GB, *FULL, 2, 20) == 0.0
