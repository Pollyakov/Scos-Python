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

2. End-to-end corrSpeckleContrast to 1e-5 relative
   Using DarkCalCollector dark cal + BrightCalCollector bright cal (all 600 frames),
   process 30 main frames spread over the recording and compare against the answer
   key `tests/data/LocalStd7x7_corr_updated.mat`, column `corrSpeckleContrast_sp600` — spVar
   computed the MATLAB way (stdfilt² in float64) from the same 600 frames minus the
   same dark mean.  What the key is and why it replaced Vika's 2024
   `LocalStd7x7_corr.mat`: see tests/test_dark_cal_offline.py's docstring.
   (Her own spVar is from the first 500 frames, with exactly our dark mean — not
   "a different dark_mean", as this file said before 2026-11-10; 500 vs 600 frames
   moves κ²_corr by +0.43 %.)

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
_GAIN_DB      = 24.0

_KEY_FILE = Path(__file__).parent / "data" / "LocalStd7x7_corr_updated.mat"   # in git; see test_dark_cal_offline.py
_KEY_RTOL = 1e-5      # app (float32 process + bright cal) vs float64 key: ≤ 3e-7 measured
_KEY_FRAMES = range(0, 600, 20)   # 30 frames spread over the whole recording

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
# Test 2 — End-to-end corrSpeckleContrast vs the updated answer key (rtol 1e-5)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
@pytest.mark.skipif(not _KEY_FILE.exists(),
                    reason="answer key missing — run tools/recheck_matlab_reference.py --write-key")
def test_bright_cal_end_to_end_matches_matlab():
    """
    Full pipeline using both DarkCalCollector and BrightCalCollector (all 600 main
    frames) reproduces the answer key's `corrSpeckleContrast_sp600` to 1e-5 per frame
    (measured ≤ 3e-7 — float32 in local_variance() and process(), todo A4).

    Before 2026-11-10 this compared with Vika's 2024 file at < 2 % (observed
    0.6–1.2 %); that file's corrected κ² differs from today's method in three ways
    that partly cancel (docs/todo.md Done item 70).
    """
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
        camera_sn    = _CAMERA_SN,
    )
    proc.scale      = _SCALE
    proc.dark_mean  = dark_mean
    proc.dark_var   = dark_var
    proc.bright_var = bright_var

    mat      = scipy.io.loadmat(str(_MAIN_DIR / "smoothingCoefficients.mat"), squeeze_me=True)
    tot_mask = mat["totMask"].astype(bool)
    key      = scipy.io.loadmat(str(_KEY_FILE), squeeze_me=True)
    assert proc.resolve_gain() == pytest.approx(float(key["G"]), rel=1e-12), (
        f"G from the gain table {proc.resolve_gain():.6f} != the key's {float(key['G']):.6f}")
    key_corr = key["corrSpeckleContrast_sp600"]
    assert len(main_tiffs) == len(key_corr) == 600

    for i in _KEY_FRAMES:
        frame = tifffile.imread(str(main_tiffs[i]))
        _, k2_corr, _ = proc.process(frame, tot_mask)
        rel_err = abs(k2_corr - key_corr[i]) / abs(key_corr[i])
        assert rel_err < _KEY_RTOL, (
            f"Frame {i}: k2_corr={k2_corr:.8g} vs answer key {key_corr[i]:.8g} "
            f"— relative error {rel_err:.2e} exceeds {_KEY_RTOL:.0e}"
        )
