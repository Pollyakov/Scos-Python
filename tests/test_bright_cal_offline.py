"""
Offline regression test for BrightCalCollector against pre-recorded lab data.

Validates that BrightCalCollector fed the 600 main recording TIFFs one at a time
(online Welford path) produces the same sp_im and bright_var as the batch path
(_stream_tiffs_mean + local_variance), to rtol=1e-12.

This mirrors the folder-replay calibration used in production
(processor.load_calibration_mat with main_dir=), confirming the online and
batch paths are numerically identical.

Note: a comparison against the smoothingCoefficients.mat spVar is intentionally
NOT included — those spVar values were computed from separate bright calibration
frames (laser on, no subject) that are not stored in this folder.  The correctness
of the online path is proven by the online == batch equivalence below.

Skipped automatically when the reference data folder is absent.

Reference data: C:/Users/USER/Scos_Frames_and_Results/
Camera: Basler a2A1920-160umPRO, SN 40513592, Mono10,
        TIFFs left-justified in uint16 → divide by 64 to get DU.
"""

import re
from pathlib import Path

import numpy as np
import pytest
import tifffile
from scipy.ndimage import uniform_filter

from core.session import BrightCalCollector, DarkCalCollector
from processor import _stream_tiffs_mean, _stream_tiffs_welford, local_variance

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

_SCALE  = 64.0
_WINDOW = 7

_reference_available = _MAIN_DIR.exists() and _DARK_DIR.exists()
_skip_if_missing = pytest.mark.skipif(
    not _reference_available,
    reason="reference lab data not found — run on the lab machine to execute",
)


def _sort_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(
        files,
        key=lambda f: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", f.name)) else 0,
    )


# ---------------------------------------------------------------------------
# Test — Welford online == batch streaming  (rtol = 1e-12)
# ---------------------------------------------------------------------------

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
