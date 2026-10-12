"""
The verdict of tools/dark_rate_ab.py — the rig's A/B test of the dark
calibration at the maximum frame rate (todo U11, plan section 3).

The tool itself needs a camera; its decision rule does not. Set B (maximum
rate) is compared with the mean of A and C (the set rate, before and after),
allowed the larger of |A - C| and 3 x the statistical error.
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from dark_rate_ab import judge, roi_stats


def _set(mean_dark, se=0.0):
    return {"mean_dark": mean_dark, "se_mean_dark": se}


class TestJudge:

    def test_b_between_a_and_c_passes(self):
        v = judge(_set(10.0), _set(10.05), _set(10.1), "mean_dark")
        assert v["pass"] and v["dev"] == pytest.approx(0.0)

    def test_b_outside_the_drift_fails(self):
        # A and C differ by 0.1 on their own; B is 0.5 away from their mean.
        v = judge(_set(10.0), _set(10.55), _set(10.1), "mean_dark")
        assert not v["pass"]
        assert v["allowed"] == pytest.approx(0.1)

    def test_a_lucky_a_equals_c_cannot_fail_b(self):
        # A = C exactly by chance: the statistical error is the floor.
        v = judge(_set(10.0, se=0.01), _set(10.02, se=0.01), _set(10.0, se=0.01),
                  "mean_dark")
        assert v["allowed"] == pytest.approx(3 * 0.01 * math.sqrt(1.5))
        assert v["pass"]

    def test_beyond_three_errors_with_no_drift_fails(self):
        v = judge(_set(10.0, se=0.01), _set(10.1, se=0.01), _set(10.0, se=0.01),
                  "mean_dark")
        assert not v["pass"]


class TestRoiStats:

    def test_means_inside_the_roi_only(self):
        mean = np.zeros((4, 4)); mean[1:3, 1:3] = 5.0
        var = np.ones((4, 4)) * 9.0; var[1:3, 1:3] = 4.0
        mask = np.zeros((4, 4), bool); mask[1:3, 1:3] = True
        s = roi_stats(mean, var, mask, n_frames=101)
        assert s["mean_dark"] == 5.0 and s["var_dark"] == 4.0 and s["pixels"] == 4

    def test_statistical_errors_follow_n_and_pixels(self):
        mask = np.ones((10, 10), bool)
        s = roi_stats(np.zeros((10, 10)), np.full((10, 10), 4.0), mask, n_frames=101)
        assert s["se_mean_dark"] == pytest.approx(math.sqrt(4.0 / (101 * 100)))
        assert s["se_var_dark"] == pytest.approx(4.0 * math.sqrt(2 / 100) / 10)
