"""
Accuracy proof for the camera-side crop (todo U6, docs/Plan_U6_Camera_Crop.md §3).

Claim: when the camera sends only `crop_for_circle()`'s rectangle instead of the
whole sensor, the calibration and κ² come out the same as on full frames — the
crop keeps CROP_MARGIN_PX (26) around the ROI circle, and no filter reads further
than window // 2 (25 at the largest window, 51) from a ROI pixel.

How: real lab frames go through the app's own path twice — DarkCalCollector,
BrightCalCollector, shrink_mask_for_window, SCOSProcessor.set_roi/process —
once on the full frames and once on the same frames cut to the crop.

Tolerances (measured 2026-10-11 while planning, plan §3):
  * per-pixel means (dark_mean, raw dark variance, spIm) — no neighbours
    involved, the same arithmetic on the same numbers: must be **identical**;
  * the filtered arrays (dark_var, spVar, float64) — `uniform_filter` keeps a
    running sum along each line, and a crop starts that line at a different
    pixel, so the last binary digits differ (≈ 1e-14 relative): rtol 1e-12;
  * κ²_raw, κ²_corr, ⟨I⟩ — process() works in float32 (todo A4): rtol 1e-6.
Anything bigger means different pixels were used — a bug, not rounding.

30 dark + 30 bright frames rather than the protocol's 600: this compares full
against cropped on the *same* frames, which the frame count does not change.

Two circles: the recording's own ROI from Mask.mat (radius 904 — larger than
the sensor is tall, so the crop is cut by the sensor's edges on three sides) and
a radius-150 circle at the same centre (a crop well inside the sensor).

Skipped when the reference data folder is absent (same data as
test_dark_cal_offline.py / test_bright_cal_offline.py).
"""

import re
from pathlib import Path

import numpy as np
import pytest
import scipy.io
import tifffile

from core.crop import CameraSteps, circle_mask, crop_for_circle
from core.session import BrightCalCollector, DarkCalCollector
from processor import SCOSProcessor, shrink_mask_for_window

_BASE     = Path("C:/Users/USER/Scos_Frames_and_Results")
_MAIN_DIR = _BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005"
_DARK_DIR = (
    _BASE
    / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
    / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
)

_SCALE     = 64.0          # 10-bit data left-justified in uint16
_CAMERA_SN = "40513592"
_BIT_DEPTH = 10
_GAIN_DB   = 24.0

N_DARK, N_BRIGHT, N_PROCESS = 30, 30, 10

# Plausible camera step rules (offset 4/2, size 4/2) so the crop is rounded,
# not just cut at the circle + margin.
_STEPS = CameraSteps(offset_x_inc=4, offset_y_inc=2, width_inc=4,
                     height_inc=2, width_min=4, height_min=1)

_reference_available = _MAIN_DIR.exists() and _DARK_DIR.exists()
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not _reference_available,
                       reason="reference lab data not found — run on the lab machine"),
]


def _sort_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(
        files,
        key=lambda f: int(m.group(1)) if (m := re.search(r"_(\d+)\.\w+$", f.name)) else 0,
    )


def _circles() -> dict[str, dict]:
    ch = scipy.io.loadmat(str(_MAIN_DIR / "Mask.mat"))["channels"][0, 0]
    # imfindcircles returns [x y] (CLAUDE.md, "Mask.mat's channels.Centers").
    cx, cy = float(ch["Centers"][0, 0]), float(ch["Centers"][0, 1])
    return {
        "Mask.mat circle": {"cx": cx, "cy": cy, "r": float(ch["Radii"][0, 0])},
        "small circle":    {"cx": cx, "cy": cy, "r": 150.0},
    }


def _processor(window, dark_mean, dark_var, bright_var):
    proc = SCOSProcessor(window_size=window, gain_db=_GAIN_DB,
                         bit_depth=_BIT_DEPTH, camera_sn=_CAMERA_SN)
    proc.scale      = _SCALE
    proc.dark_mean  = dark_mean
    proc.dark_var   = dark_var
    proc.bright_var = bright_var
    return proc


@pytest.mark.parametrize("window", [7, 51])
def test_cropped_frames_give_the_same_calibration_and_kappa2(window):
    dark_tiffs = _sort_tiffs(_DARK_DIR)[:N_DARK]
    main_tiffs = _sort_tiffs(_MAIN_DIR)[:N_BRIGHT]
    sensor_h, sensor_w = tifffile.imread(str(main_tiffs[0])).shape
    circles = _circles()
    crops = {name: crop_for_circle(c["cx"], c["cy"], c["r"], sensor_w, sensor_h, _STEPS)
             for name, c in circles.items()}
    cuts = {name: (slice(c.offset_y, c.offset_y + c.height),
                   slice(c.offset_x, c.offset_x + c.width))
            for name, c in crops.items()}

    # --- Calibration: one full-frame collector, one per crop, same frames ---
    dark = {"full": DarkCalCollector(N_DARK, window)}
    dark.update({name: DarkCalCollector(N_DARK, window) for name in crops})
    for tp in dark_tiffs:
        frame = tifffile.imread(str(tp)).astype(np.float64) / _SCALE
        dark["full"].add_frame(frame)
        for name, cut in cuts.items():
            dark[name].add_frame(frame[cut])
    dark_res = {k: col.result() for k, col in dark.items()}    # (dark_mean, dark_var)

    bright = {"full": BrightCalCollector(N_BRIGHT, window)}
    bright.update({name: BrightCalCollector(N_BRIGHT, window) for name in crops})
    for tp in main_tiffs:
        frame = tifffile.imread(str(tp)).astype(np.float64) / _SCALE
        bright["full"].add_frame(frame)
        for name, cut in cuts.items():
            bright[name].add_frame(frame[cut])
    bright_res = {k: bright[k].result(dark_mean=dark_res[k][0]) for k in bright}  # (spIm, spVar)

    report = []
    for name, circ in circles.items():
        crop, cut = crops[name], cuts[name]
        full_mask = circle_mask((sensor_h, sensor_w), circ)
        crop_mask = circle_mask(crop.shape, circ, (crop.offset_x, crop.offset_y))
        # Every ROI pixel is inside the crop — nothing of the circle cut away.
        assert crop_mask.sum() == full_mask.sum(), name

        def on_roi(full_arr, crop_arr):
            """The two arrays at the ROI's pixels, full one cut to the crop."""
            return full_arr[cut][crop_mask], crop_arr[crop_mask]

        # Per-pixel means: no neighbours involved → identical.
        for label, f_arr, c_arr in [
                ("dark_mean", dark_res["full"][0], dark_res[name][0]),
                ("spIm",      bright_res["full"][0], bright_res[name][0])]:
            f, c = on_roi(f_arr, c_arr)
            np.testing.assert_array_equal(c, f, err_msg=f"{name}, window {window}: {label}")

        # Filtered arrays: same pixels, rounding in the running sum only.
        for label, f_arr, c_arr in [
                ("dark_var", dark_res["full"][1], dark_res[name][1]),
                ("spVar",    bright_res["full"][1], bright_res[name][1])]:
            f, c = on_roi(f_arr, c_arr)
            np.testing.assert_allclose(c, f, rtol=1e-12, atol=0,
                                       err_msg=f"{name}, window {window}: {label}")
            rel = np.max(np.abs(c - f) / np.abs(f)) if f.size else 0.0
            report.append(f"{name:16s} w={window:2d} {label:8s} max rel diff {rel:.1e}")

        # κ² through the processor, as the app runs it: mask shrunk for the
        # window, ROI box set once, then process() on each frame.
        full_scos = shrink_mask_for_window(full_mask, window)
        crop_scos = shrink_mask_for_window(crop_mask, window)
        assert full_scos.sum() > 0 and crop_scos.sum() == full_scos.sum(), name
        p_full = _processor(window, dark_res["full"][0], dark_res["full"][1], bright_res["full"][1])
        p_crop = _processor(window, dark_res[name][0], dark_res[name][1], bright_res[name][1])
        p_full.set_roi(full_scos)
        p_crop.set_roi(crop_scos)

        worst = np.zeros(3)
        for tp in main_tiffs[:N_PROCESS]:
            frame = tifffile.imread(str(tp))
            r_full = np.array(p_full.process(frame, full_scos))
            r_crop = np.array(p_crop.process(frame[cut], crop_scos))
            np.testing.assert_allclose(
                r_crop, r_full, rtol=1e-6, atol=0,
                err_msg=f"{name}, window {window}: (κ²_raw, κ²_corr, ⟨I⟩), {tp.name}")
            worst = np.maximum(worst, np.abs(r_crop - r_full) / np.abs(r_full))
        report.append(f"{name:16s} w={window:2d} crop {crop.width}x{crop.height} at "
                      f"({crop.offset_x},{crop.offset_y}); max rel diff k2_raw "
                      f"{worst[0]:.1e}, k2_corr {worst[1]:.1e}, <I> {worst[2]:.1e}")

    # Shown with `pytest -s`: the margins actually seen. ASCII only — the
    # Windows console (cp1252) cannot print κ and the print would fail the test.
    print("\n" + "\n".join(report))
