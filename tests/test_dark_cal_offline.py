"""
Offline regression test for DarkCalCollector against pre-recorded lab data.

Validates two claims:
  1. Welford consistency — DarkCalCollector fed the 600 dark TIFFs one at a time
     produces the same dark_mean and dark_var_filtered as the batch streaming path
     (_stream_tiffs_welford + uniform_filter used in processor.load_calibration_mat),
     to rtol=1e-12.

  2. End-to-end accuracy — using the Welford dark cal and Vika's spVar to process the
     main recording reproduces the answer key `tests/data/LocalStd7x7_corr_updated.mat` to 1e-5
     relative (κ²_corr, κ²_raw) on 30 frames spread over the recording.

The answer key (2026-11-10, docs/todo.md Done item 70): Vika's own MATLAB result
`LocalStd7x7_corr.mat` (Ver2, 2024-11-01), rebuilt exactly (< 1e-12) by
`tools/recheck_matlab_reference.py`, then brought up to today's method in three named
steps — spVar/darkVar on the right pixels (her script indexed them with the cut mask),
G from the measured table (she had typed 1.468449; the table gives 1.459666), and
⟨I⟩ = the 7×7 window mean (SCOS_protocol.md:53) instead of her fitted image. Her old
file agreed with the app only to ~1–2 % because those three differences partly cancel.
After the update the app differs by float32 rounding only (≤ 2e-7 measured), so the
test guards the math against future breakage; the link to MATLAB is the exact rebuild.
Regenerate with `python tools/recheck_matlab_reference.py --write-key`.

Skipped automatically when the reference data folder or the key is absent (e.g. in CI).

Reference data: C:/Users/USER/Scos_Frames_and_Results/
  expT5ms_Gain24dB_BL100DU_FR40Hz_005/        — main recording TIFFs + MATLAB results
  expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark/   — 600 dark-frame TIFFs
Camera: Basler a2A1920-160umPRO, SN 40513592, Mono10 (G from CamerasMeasuredGain.csv),
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
_GAIN_DB      = 24.0      # from folder name: Gain24dB

# The answer key, kept in git (see module docstring); the tool also leaves a copy
# beside Vika's file in the lab data folder.
_KEY_FILE = Path(__file__).parent / "data" / "LocalStd7x7_corr_updated.mat"
_KEY_RTOL = 1e-5      # float32 process() vs float64 key: ≤ 2e-7 measured (2026-11-10)
_KEY_FRAMES = range(0, 600, 20)   # 30 frames spread over the whole recording

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
# Test 2 — End-to-end κ² vs the updated answer key (rtol 1e-5)
# ---------------------------------------------------------------------------

@_slow
@_skip_if_missing
@pytest.mark.skipif(not _KEY_FILE.exists(),
                    reason="answer key missing — run tools/recheck_matlab_reference.py --write-key")
def test_dark_cal_end_to_end_matches_matlab():
    """
    DarkCalCollector dark cal + Vika's spVar (smoothingCoefficients.mat), the app's
    SCOSProcessor on 30 frames spread over the main recording, against the updated
    answer key's `corrSpeckleContrast` (her spVar), `rawSpeckleContrast` and `meanVec`.

    Tolerance 1e-5 relative (measured ≤ 2e-7: float32 in process(), todo A4). The old
    2 % — needed against her 2024 file — would have let a 1 % slip in the math through.
    """
    # --- Dark calibration via DarkCalCollector ---
    dark_tiffs = _sort_tiffs(_DARK_DIR)
    col = DarkCalCollector(n_frames=len(dark_tiffs), window_size=_WINDOW)
    for tp in dark_tiffs:
        col.add_frame(tifffile.imread(str(tp)).astype(np.float64) / _SCALE)
    dark_mean, dark_var = col.result()

    # --- Bright calibration: spVar from Vika's smoothingCoefficients.mat ---
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
        camera_sn    = _CAMERA_SN,
    )
    proc.scale      = _SCALE
    proc.dark_mean  = dark_mean
    proc.dark_var   = dark_var
    proc.bright_var = sp_var

    # --- Answer key ---
    key = scipy.io.loadmat(str(_KEY_FILE), squeeze_me=True)
    # The key was built with the table's G; a changed gain table must fail here,
    # by name, rather than as a κ² mismatch.
    assert proc.resolve_gain() == pytest.approx(float(key["G"]), rel=1e-12), (
        f"G from the gain table {proc.resolve_gain():.6f} != the key's {float(key['G']):.6f}")

    main_tiffs = _sort_tiffs(_MAIN_DIR)
    assert len(main_tiffs) == len(key["corrSpeckleContrast"]) == 600
    for i in _KEY_FRAMES:
        frame = tifffile.imread(str(main_tiffs[i]))       # raw uint16
        k2_raw, k2_corr, mean_i = proc.process(frame, tot_mask)

        for name, got, want in (("k2_corr", k2_corr, key["corrSpeckleContrast"][i]),
                                ("k2_raw", k2_raw, key["rawSpeckleContrast"][i]),
                                ("<I>", mean_i, key["meanVec"][i])):
            rel_err = abs(got - want) / abs(want)
            assert rel_err < _KEY_RTOL, (
                f"Frame {i}: {name}={got:.8g} vs answer key {want:.8g} "
                f"— relative error {rel_err:.2e} exceeds {_KEY_RTOL:.0e}"
            )
