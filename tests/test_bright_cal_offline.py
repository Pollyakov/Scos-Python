"""
Offline regression tests for BrightCalCollector against pre-recorded lab data.

The 600 main recording TIFFs (laser on, with subject) are also used as the bright
calibration source — confirmed by the lab operator.  smoothingCoefficients.mat
(spIm, spVar) in the same folder was produced by MATLAB from those same frames.

Two tests:

1. Welford online == batch (rtol=1e-9 / atol=1e-10)
   BrightCalCollector fed the 600 TIFFs one at a time produces the same sp_im and
   bright_var as the batch path (_stream_tiffs_mean + local_variance).  Proves the
   online algorithm is numerically correct independent of MATLAB.

2. End-to-end corrSpeckleContrast < 2 % per frame
   Using DarkCalCollector dark cal + BrightCalCollector bright cal, process 10 main
   frames and compare against MATLAB's LocalStd7x7_corr.mat.  Our spVar gives
   0.6–1.2 % error (slightly better than smoothingCoefficients.mat's ~1 % because
   our dark_mean and sp_im are internally consistent).

Both tests are skipped automatically when the reference data folder is absent.

Reference data: C:/Users/USER/Scos_Frames_and_Results/
Camera: Basler a2A1920-160umPRO, SN 40513592, Mono10, gain 24 dB,
        TIFFs left-justified in uint16 → divide by 64 to get DU.
"""

import re
from pathlib import Path

import numpy as np
import pytest
import scipy.io
import tifffile
from scipy.ndimage import uniform_filter

from core.session import BrightCalCollector, DarkCalCollector
from processor import SCOSProcessor, _stream_tiffs_mean, _stream_tiffs_welford, local_variance

# ---------------------------------------------------------------------------
# Reference-data paths and constants
# ---------------------------------------------------------------------------

_BASE     = Path("C:/Users/USER/Scos_Frames_and_Results")
_MAIN_DIR = _BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005"
_DARK_DIR = (
    _BASE
    / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
    / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
)

_SCALE        = 64.0
_WINDOW       = 7
_CAMERA_SN    = "40513592"
_BIT_DEPTH    = 10
_SAT_CAPACITY = 11117.0
_GAIN_DB      = 24.0

_reference_available = _MAIN_DIR.exists() and _DARK_DIR.exists()
_skip_if_missing = pytest.mark.skipif(
    not _reference_available,
    reason="reference lab data not found — run on the lab machine to execute",
)
_slow = pytest.mark.slow


def _sort_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(
        files,
        key=lambda f: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", f.name)) else 0,
    )


# ---------------------------------------------------------------------------
# Test — Welford online == batch streaming  (rtol = 1e-12)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
def test_bright_cal_welford_matches_batch():
    """
    BrightCalCollector (online Welford mean) must produce identical sp_im and
    bright_var to the existing _stream_tiffs_mean + local_variance batch path,
    given the same 600 main recording TIFFs and the same dark_mean.

    Both paths divide pixel values by _SCALE=64 before accumulating.
    """
    main_tiffs = _sort_tiffs(_MAIN_DIR)
    dark_tiffs = _sort_tiffs(_DARK_DIR)
    assert len(main_tiffs) == 600, f"Expected 600 main TIFFs, found {len(main_tiffs)}"
    assert len(dark_tiffs) == 600, f"Expected 600 dark TIFFs, found {len(dark_tiffs)}"

    # Compute dark_mean via the existing batch path (already validated by dark cal test)
    dark_mean, _ = _stream_tiffs_welford(dark_tiffs, scale=_SCALE)

    # --- Online: BrightCalCollector ---
    col = BrightCalCollector(n_frames=len(main_tiffs), window_size=_WINDOW)
    for tp in main_tiffs:
        col.add_frame(tifffile.imread(str(tp)).astype(np.float64) / _SCALE)
    sp_im_online, bright_var_online = col.result(dark_mean=dark_mean)

    # --- Batch: _stream_tiffs_mean + local_variance (existing code path) ---
    mean_bright_batch = _stream_tiffs_mean(main_tiffs, scale=_SCALE)
    sp_im_batch       = mean_bright_batch - dark_mean
    _, bright_var_batch = local_variance(sp_im_batch, _WINDOW)

    # Welford and simple-sum/N accumulate in different floating-point orders.
    # Max observed absolute difference: ~1.5e-13 DU (sub-femto — pure rounding).
    # Near-zero sp_im pixels (laser edge, near-dark areas) make rtol blow up,
    # so we use atol=1e-10 as the governing tolerance.
    np.testing.assert_allclose(
        sp_im_online, sp_im_batch,
        atol=1e-10, rtol=0,
        err_msg="sp_im: Welford online differs from batch streaming",
    )
    np.testing.assert_allclose(
        bright_var_online, bright_var_batch,
        atol=1e-10, rtol=0,
        err_msg="bright_var: Welford online differs from batch streaming",
    )


# ---------------------------------------------------------------------------
# Test 2 — End-to-end corrSpeckleContrast vs MATLAB reference (< 2 %)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
def test_bright_cal_end_to_end_matches_matlab():
    """
    Full pipeline using both DarkCalCollector and BrightCalCollector reproduces
    MATLAB's corrSpeckleContrast to < 2 % per frame.

    Observed: 0.6–1.2 % error.  The spVar values differ from smoothingCoefficients.mat
    by ~4 % in mean because MATLAB's .mat was generated in a different run with a
    slightly different dark_mean.  Internally consistent dark+bright cal gives
    better end-to-end accuracy than mixing our dark_mean with MATLAB's spVar.
    """
    N_FRAMES   = 10
    main_tiffs = _sort_tiffs(_MAIN_DIR)
    dark_tiffs = _sort_tiffs(_DARK_DIR)

    # Dark calibration via DarkCalCollector
    dark_col = DarkCalCollector(n_frames=len(dark_tiffs), window_size=_WINDOW)
    for tp in dark_tiffs:
        dark_col.add_frame(tifffile.imread(str(tp)).astype(np.float64) / _SCALE)
    dark_mean, dark_var = dark_col.result()

    # Bright calibration via BrightCalCollector (all 600 main frames)
    bright_col = BrightCalCollector(n_frames=len(main_tiffs), window_size=_WINDOW)
    for tp in main_tiffs:
        bright_col.add_frame(tifffile.imread(str(tp)).astype(np.float64) / _SCALE)
    _, bright_var = bright_col.result(dark_mean=dark_mean)

    # Processor setup
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
    proc.bright_var = bright_var

    mat      = scipy.io.loadmat(str(_MAIN_DIR / "smoothingCoefficients.mat"), squeeze_me=True)
    tot_mask = mat["totMask"].astype(bool)
    ref      = scipy.io.loadmat(str(_MAIN_DIR / "LocalStd7x7_corr.mat"), squeeze_me=True)
    matlab_corr = ref["corrSpeckleContrast"]

    for i, tp in enumerate(main_tiffs[:N_FRAMES]):
        frame = tifffile.imread(str(tp))
        _, k2_corr, _ = proc.process(frame, tot_mask)
        rel_err = abs(k2_corr - matlab_corr[i]) / abs(matlab_corr[i])
        assert rel_err < 0.02, (
            f"Frame {i}: k2_corr={k2_corr:.6f} vs MATLAB={matlab_corr[i]:.6f} "
            f"— relative error {rel_err*100:.2f}% exceeds 2%"
        )
