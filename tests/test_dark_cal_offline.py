"""
Offline regression test for DarkCalCollector against pre-recorded lab data.

Validates two claims:
  1. Welford consistency — DarkCalCollector fed the 600 dark TIFFs one at a time
     produces the same dark_mean and dark_var_filtered as the batch streaming path
     (_stream_tiffs_welford + uniform_filter used in processor.load_calibration_mat),
     to rtol=1e-12.

  2. End-to-end accuracy — using the Welford dark cal to process the main recording
     reproduces MATLAB's corrSpeckleContrast to < 2 % relative error on each frame
     (same tolerance as the existing offline validation).

Skipped automatically when the reference data folder is absent (e.g. in CI).

Reference data: C:/Users/USER/Scos_Frames_and_Results/
  expT5ms_Gain24dB_BL100DU_FR40Hz_005/        — main recording TIFFs + MATLAB results
  expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark/   — 600 dark-frame TIFFs
Camera: Basler a2A1920-160umPRO, SN 40513592, Mono10, sat_capacity=11117 e-,
        TIFFs store 10-bit data left-justified in uint16 → divide by 64 to get DU.
"""

import re
from pathlib import Path

import numpy as np
import pytest
import scipy.io
import tifffile
from scipy.ndimage import uniform_filter

from core.session import DarkCalCollector
from processor import SCOSProcessor, _stream_tiffs_welford

# ---------------------------------------------------------------------------
# Reference-data paths
# ---------------------------------------------------------------------------

_BASE = Path("C:/Users/USER/Scos_Frames_and_Results")
_MAIN_DIR = _BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005"
_DARK_OUTER = _BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
_DARK_DIR   = _DARK_OUTER / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"

# Camera / acquisition parameters (from CLAUDE.md and info struct in the .mat)
_SCALE        = 64.0      # 10-bit left-justified in uint16
_WINDOW       = 7
_CAMERA_SN    = "40513592"
_BIT_DEPTH    = 10
_SAT_CAPACITY = 11117.0
_GAIN_DB      = 24.0      # from folder name: Gain24dB

_reference_available = _DARK_DIR.exists() and _MAIN_DIR.exists()
_skip_if_missing = pytest.mark.skipif(
    not _reference_available,
    reason="reference lab data not found — run on the lab machine to execute",
)
_slow = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sort_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(
        files,
        key=lambda f: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", f.name)) else 0,
    )


# ---------------------------------------------------------------------------
# Test 1 — Welford online == batch streaming (rtol = 1e-12)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
def test_dark_cal_welford_matches_batch():
    """
    DarkCalCollector (online Welford) must produce identical dark_mean and
    dark_var_filtered to the existing _stream_tiffs_welford + uniform_filter
    batch path, given the same 600 dark TIFFs.

    Both divide pixel values by _SCALE=64 before accumulating (simulating
    what the live camera delivers in Mono10: 10-bit values, no scale needed).
    """
    dark_tiffs = _sort_tiffs(_DARK_DIR)
    assert len(dark_tiffs) == 600, f"Expected 600 dark TIFFs, found {len(dark_tiffs)}"

    # --- Online: DarkCalCollector ---
    col = DarkCalCollector(n_frames=len(dark_tiffs), window_size=_WINDOW)
    for tiff_path in dark_tiffs:
        raw = tifffile.imread(str(tiff_path)).astype(np.float64) / _SCALE
        col.add_frame(raw)

    mean_online, var_online = col.result()

    # --- Batch: _stream_tiffs_welford + uniform_filter (existing code path) ---
    mean_batch, raw_var_batch = _stream_tiffs_welford(dark_tiffs, scale=_SCALE)
    var_batch = uniform_filter(raw_var_batch, size=_WINDOW)

    np.testing.assert_allclose(
        mean_online, mean_batch,
        rtol=1e-12,
        err_msg="dark_mean: Welford online differs from batch streaming",
    )
    np.testing.assert_allclose(
        var_online, var_batch,
        rtol=1e-12,
        err_msg="dark_var_filtered: Welford online differs from batch streaming",
    )


# ---------------------------------------------------------------------------
# Test 2 — End-to-end corrSpeckleContrast vs MATLAB reference (< 2 %)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
def test_dark_cal_end_to_end_matches_matlab():
    """
    Using DarkCalCollector dark cal + smoothingCoefficients.mat spVar,
    process the first N_FRAMES main recording frames and compare
    corrSpeckleContrast against MATLAB's LocalStd7x7_corr.mat.

    Tolerance: < 2 % relative error per frame, matching the existing offline
    validation result documented in CLAUDE.md (1.2 % average error on κ²_corr).
    """
    N_FRAMES = 10   # enough to validate; keep the test fast

    # --- Dark calibration via DarkCalCollector ---
    dark_tiffs = _sort_tiffs(_DARK_DIR)
    col = DarkCalCollector(n_frames=len(dark_tiffs), window_size=_WINDOW)
    for tp in dark_tiffs:
        col.add_frame(tifffile.imread(str(tp)).astype(np.float64) / _SCALE)
    dark_mean, dark_var = col.result()

    # --- Bright calibration: load spVar from smoothingCoefficients.mat ---
    smooth_mat = scipy.io.loadmat(
        str(_MAIN_DIR / "smoothingCoefficients.mat"), squeeze_me=True
    )
    sp_var  = smooth_mat["spVar"].astype(np.float64)
    tot_mask = smooth_mat["totMask"].astype(bool)

    # --- Processor setup ---
    proc = SCOSProcessor(
        window_size  = _WINDOW,
        gain_db      = _GAIN_DB,
        bit_depth    = _BIT_DEPTH,
        sat_capacity = _SAT_CAPACITY,
        camera_sn    = _CAMERA_SN,
    )
    proc.scale      = _SCALE
    proc.dark_mean  = dark_mean
    proc.dark_var   = dark_var
    proc.bright_var = sp_var

    # --- MATLAB reference ---
    ref = scipy.io.loadmat(
        str(_MAIN_DIR / "LocalStd7x7_corr.mat"), squeeze_me=True
    )
    matlab_corr = ref["corrSpeckleContrast"]   # shape (600,)

    # --- Process first N_FRAMES main frames ---
    main_tiffs = _sort_tiffs(_MAIN_DIR)[:N_FRAMES]
    for i, tp in enumerate(main_tiffs):
        frame = tifffile.imread(str(tp))       # raw uint16
        _, k2_corr, _ = proc.process(frame, tot_mask)

        rel_err = abs(k2_corr - matlab_corr[i]) / abs(matlab_corr[i])
        assert rel_err < 0.02, (
            f"Frame {i}: k2_corr={k2_corr:.6f} vs MATLAB={matlab_corr[i]:.6f} "
            f"— relative error {rel_err*100:.2f}% exceeds 2%"
        )
