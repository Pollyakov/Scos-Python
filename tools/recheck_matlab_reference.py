"""Re-check the app's MATLAB accuracy reference and build the updated answer key (2026-11-10).

The slow offline tests used to compare the app's corrected κ² with `corrSpeckleContrast` in
`LocalStd7x7_corr.mat`, which Vika's Ver2 script made on 2024-11-01 from the lab recording
expT5ms_Gain24dB_BL100DU_FR40Hz_005 (600 frames).  That file differs from today's method in
three ways (docs/todo.md, Done item 70):
  1. wrong pixels — her line `spVar(masks_cut)` / `darkVar(masks_cut)` indexes the full-size
     maps with the *cut* mask (Ver2 line 447 then, 427 now);
  2. her hand-typed G = 0.5846·10^(8/20) = 1.468449, not the measured table's 1.459666
     (today's LoadG.m and the app both rescale the 20 dB row);
  3. her fitted intensity image (FitMeanIm) for ⟨I⟩, not the window's own 7×7 mean, which
     docs/SCOS_protocol.md:53 asks for and the app uses.

What this script does
  a. rebuilds her calculation in Python (float64) from her own saved maps
     (smoothingCoefficients.mat: spVar, fitI_A, fitI_B, totMask; Mask.mat: masks) and the 600
     dark TIFFs, and checks that ⟨I⟩, raw κ² and — on the wrong pixels, with her G — corrected
     κ² come out the same as her file (to 1e-12 relative); finds her G from the data;
  b. applies the three changes, one at a time, and prints how far each moves κ²_corr;
  c. compares the app (SCOSProcessor, both test set-ups) with the old and the new answer key;
  d. with --write-key, and only if step a passed, writes the new answer key
     `LocalStd7x7_corr_updated.mat` to tests/data/ (in git — the slow tests read this
     one) and a copy beside her file (hers is never touched).

The new key's columns (600 frames each):
  corrSpeckleContrast        her calculation + the three changes, her spVar (500 frames)
  corrSpeckleContrast_sp600  the same, spVar = stdfilt(mean of all 600 frames − dark mean)^2,
                             as today's Ver2 computes it (numFramesForSPNoise = 600)
  rawSpeckleContrast         mean(stdfilt² / (7×7 mean)²) over the mask
  meanVec                    mean of (frame − dark mean) over the mask
  G, provenance

Usage:  python tools/recheck_matlab_reference.py [--write-key] [--out <per-frame.npz>]
≈ 9 minutes.  Read-only unless --write-key.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import scipy.io
import tifffile
from scipy.ndimage import uniform_filter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from processor import (SCOSProcessor, _sort_tiffs, _stream_tiffs_welford,  # noqa: E402
                       local_variance)

BASE = Path("C:/Users/USER/Scos_Frames_and_Results")
MAIN = BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005"
DARK = BASE / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark" / "expT5ms_Gain24dB_BL100DU_FR40Hz_005_dark"
HER_FILE = MAIN / "LocalStd7x7_corr.mat"
KEY_FILE = MAIN / "LocalStd7x7_corr_updated.mat"                    # beside her file
REPO_KEY_FILE = Path(__file__).resolve().parents[1] / "tests" / "data" / KEY_FILE.name  # in git; the tests read this one
SCALE, BL, W = 64.0, 100.0, 7
N = W * W
G_HER_TYPED = 0.5846 * 10 ** ((24 - 16) / 20)   # Ver2 3a914ac: 'Nadav06' camera, Mono10
G_FORMULA = 2 ** 10 / 10.5e3 * 10 ** (24 / 20)  # Ver2 81cadaf fallback: ConvertGain(24,10,10.5e3)
REPRO_TOL = 1e-12                               # rebuild of her file must be this exact

PROVENANCE = (
    "Updated answer key for the app's offline accuracy tests, written by "
    "tools/recheck_matlab_reference.py (Scos-Python). Vika's LocalStd7x7_corr.mat "
    "(Ver2, 2024-11-01) rebuilt in Python from her smoothingCoefficients.mat, Mask.mat and the "
    "600 dark TIFFs (reproduced to <1e-12), then three changes: (1) spVar/darkVar on the right "
    "pixels, (2) G from CamerasMeasuredGain.csv (20 dB row rescaled to 24 dB), (3) <I> = the 7x7 "
    "window mean (SCOS_protocol.md:53) instead of FitMeanIm. See docs/todo.md Done item 70."
)


def stdfilt2(a: np.ndarray) -> np.ndarray:
    """MATLAB stdfilt(a, true(W)).^2 — symmetric padding (= scipy 'reflect'), N-1."""
    m = uniform_filter(a, W, mode="reflect")
    return np.maximum(uniform_filter(a * a, W, mode="reflect") - m * m, 0) * N / (N - 1)


def rel(a, b):
    return (np.asarray(a) - np.asarray(b)) / np.abs(np.asarray(b))


def summary(name: str, x, ref, first=10) -> str:
    r = rel(x, ref) * 100
    return (f"{name:58s} mean of frames {r.mean():+9.5f} %   |per frame| max {np.abs(r).max():9.5f} %"
            f"   first {first}: max {np.abs(r[:first]).max():9.5f} %")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-key", action="store_true",
                    help=f"write {KEY_FILE.name} to tests/data/ and beside her file, "
                         "if the rebuild is exact")
    ap.add_argument("--out", type=Path, help="save all per-frame numbers to this .npz")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")   # κ, ≈, ² on a Windows console
    t0 = time.time()

    # ---------------- her saved maps ----------------
    sc = scipy.io.loadmat(MAIN / "smoothingCoefficients.mat", squeeze_me=True)
    sp_var_her = sc["spVar"].astype(np.float64)
    sp_im_her = sc["spIm"].astype(np.float64)
    fit_a, fit_b = sc["fitI_A"].astype(np.float64), sc["fitI_B"].astype(np.float64)
    tot_mask = sc["totMask"] > 0
    masks = scipy.io.loadmat(MAIN / "Mask.mat", squeeze_me=True)["masks"] > 0
    ws2 = int(np.ceil(W / 2))
    masks[:ws2, :] = masks[-ws2:, :] = False
    masks[:, :ws2] = masks[:, -ws2:] = False
    H, Wd = tot_mask.shape
    print(f"totMask {tot_mask.sum()} px, masks after margins {masks.sum()} px, "
          f"identical: {bool((masks == tot_mask).all())}")

    ref = scipy.io.loadmat(HER_FILE, squeeze_me=True)
    ref_corr, ref_raw, ref_I = ref["corrSpeckleContrast"], ref["rawSpeckleContrast"], ref["meanVec"]

    # ---------------- 'Decrease Image Size' (Ver2), 0-based inclusive ----------------
    y, x = np.nonzero(tot_mask)
    y0, y1 = max(y.min() - W, 0), min(y.max() + W, H - 1)
    x0, x1 = max(x.min() - W, 0), min(x.max() + W, Wd - 1)
    cut = (slice(y0, y1 + 1), slice(x0, x1 + 1))
    mc = masks[cut]
    lin = np.flatnonzero(mc.ravel(order="F"))          # MATLAB a(mask_cut): column-major order
    print(f"frame {H}x{Wd}; cut rows {y0}-{y1}, cols {x0}-{x1}")

    def sel(a_cut):                                     # a_cut(masks_cut)
        return a_cut.ravel(order="F")[lin]

    def wrong(full):                                    # her line: fullMap(masks_cut)
        return full.ravel(order="F")[lin]

    def right(full):                                    # fixed:   fullMap(roi.y,roi.x)(masks_cut)
        return sel(full[cut])

    # ---------------- dark: mean and variance from the 600 dark TIFFs ----------------
    dark_files = _sort_tiffs(DARK)
    dark_mean, dark_var_px = _stream_tiffs_welford(dark_files, scale=SCALE)
    background = dark_mean - BL
    dark_var_her = uniform_filter(dark_var_px, W, mode="nearest")   # imboxfilt (replicate)
    dark_var_app = uniform_filter(dark_var_px, W)                  # app (reflect)
    print(f"dark: {len(dark_files)} frames, ROI mean dark var {right(dark_var_her).mean():.4f} DU², "
          f"wrong pixels {wrong(dark_var_her).mean():.4f}   ({time.time()-t0:.0f} s)")

    # ---------------- main frames: means of first 500 / all 600 ----------------
    main_files = _sort_tiffs(MAIN)
    nF = len(main_files)
    acc = np.zeros((H, Wd))
    mean500 = None
    for i, f in enumerate(main_files):
        acc += tifffile.imread(f).astype(np.float64) / SCALE
        if i + 1 == 500:
            mean500 = acc / 500
    mean600 = acc / nF
    sp_im_err = np.abs((mean500 - BL - background) - sp_im_her)[masks].max()
    print(f"her spIm rebuilt from the first 500 frames: max |diff| in mask {sp_im_err:.3g} DU")
    sp_var_600 = stdfilt2(mean600 - dark_mean)                       # key, today's Ver2 way, float64
    _, sp_var_app = local_variance(mean600 - dark_mean, W)           # app's bright cal (float32)
    sp_var_app = sp_var_app.astype(np.float64)
    print(f"spVar ROI mean: hers (500) {right(sp_var_her).mean():.4f}, wrong pixels "
          f"{wrong(sp_var_her).mean():.4f}, 600 frames {right(sp_var_600).mean():.4f}")

    # ---------------- app processors (the two test set-ups) ----------------
    def make_proc(bright_var):
        p = SCOSProcessor(window_size=W, gain_db=24.0, bit_depth=10, camera_sn="40513592")
        p.scale, p.dark_mean, p.dark_var, p.bright_var = SCALE, dark_mean, dark_var_app, bright_var
        return p
    proc1 = make_proc(sp_var_her)   # test_dark_cal_end_to_end: her spVar
    proc2 = make_proc(sp_var_app)   # test_bright_cal_end_to_end: app's bright cal
    G_app = proc1.resolve_gain()

    # ---------------- per frame ----------------
    q = {k: np.empty(nF) for k in (
        "I", "raw_her", "inv_fI", "S_wrong", "S_right",      # her method; corr = S - G*inv_fI
        "raw_lm", "S_right_lm", "inv_lm", "S_right_lm_sp600",  # window mean for <I>
        "app1_corr", "app1_raw", "app1_I", "app2_corr")}
    c_sp_w, c_sp_r, c_sp6 = wrong(sp_var_her), right(sp_var_her), right(sp_var_600)
    c_dv_w, c_dv_r = wrong(dark_var_her), right(dark_var_her)
    c_a, c_b = sel(fit_a[cut]), sel(fit_b[cut])
    for i, f in enumerate(main_files):
        fr = tifffile.imread(f)
        im = (fr.astype(np.float64) / SCALE - BL - background)[cut]
        v = sel(stdfilt2(im))
        m_loc = sel(uniform_filter(im, W, mode="reflect"))
        m2 = m_loc * m_loc
        I = sel(im).mean()
        fI = c_a * I + c_b
        fI2 = fI * fI
        q["I"][i] = I
        q["raw_her"][i] = np.mean(v / fI2)
        q["inv_fI"][i] = np.mean(1 / fI)
        q["S_wrong"][i] = np.mean((v - c_sp_w - 1 / 12 - c_dv_w) / fI2)
        q["S_right"][i] = np.mean((v - c_sp_r - 1 / 12 - c_dv_r) / fI2)
        q["raw_lm"][i] = np.mean(v / m2)
        q["S_right_lm"][i] = np.mean((v - c_sp_r - 1 / 12 - c_dv_r) / m2)
        q["inv_lm"][i] = np.mean(1 / m_loc)
        q["S_right_lm_sp600"][i] = np.mean((v - c_sp6 - 1 / 12 - c_dv_r) / m2)
        q["app1_raw"][i], q["app1_corr"][i], q["app1_I"][i] = proc1.process(fr, tot_mask)
        q["app2_corr"][i] = proc2.process(fr, tot_mask)[1]
        if i % 100 == 0:
            print(f"  frame {i}  ({time.time()-t0:.0f} s)", flush=True)
    if args.out:   # save first: the loop above is the slow part
        np.savez(args.out, ref_corr=ref_corr, ref_raw=ref_raw, ref_I=ref_I, G_app=G_app, **q)

    # ---------------- a. her run rebuilt ----------------
    print("\n=== a. Her run rebuilt ===")
    G_frames = (q["S_wrong"] - ref_corr) / q["inv_fI"]
    G_her = float(np.median(G_frames))
    her_wrong = q["S_wrong"] - G_her * q["inv_fI"]
    checks = {
        "spIm (DU)": sp_im_err,
        "<I> (DU)": np.abs(q["I"] - ref_I).max(),
        "raw κ² (relative)": np.abs(rel(q["raw_her"], ref_raw)).max(),
        "corrected κ², wrong pixels, her G (relative)": np.abs(rel(her_wrong, ref_corr)).max(),
        "her G, spread over frames": G_frames.max() - G_frames.min(),
        "her G vs hand-typed value": abs(G_her - G_HER_TYPED),
    }
    exact = True
    for k, v in checks.items():
        ok = v < REPRO_TOL
        exact &= ok
        print(f"  {k:46s} max diff {v:.3g}   {'OK' if ok else 'NOT EXACT'}")
    print(f"  her G {G_her:.6f} (hand-typed {G_HER_TYPED:.6f}; formula {G_FORMULA:.6f}); "
          f"table / app {G_app:.6f}")

    # ---------------- b. the three changes, one at a time ----------------
    print("\n=== b. The three changes, one at a time (each vs the line before) ===")
    s0 = ref_corr
    s1 = q["S_right"] - G_her * q["inv_fI"]
    s2 = q["S_right"] - G_app * q["inv_fI"]
    key_corr = q["S_right_lm"] - G_app * q["inv_lm"]
    key_corr_600 = q["S_right_lm_sp600"] - G_app * q["inv_lm"]
    print(summary("1) right pixels", s1, s0))
    print(summary("2) table G", s2, s1))
    print(summary("3) window mean for <I>  -> NEW KEY", key_corr, s2))
    print(summary("   new key vs her file (all three)", key_corr, s0))
    print(summary("   spVar from 600 frames vs her 500 (key _sp600)", key_corr_600, key_corr))

    # ---------------- c. app vs old and new key ----------------
    print("\n=== c. App vs answer key ===")
    print(summary("app, her spVar        vs OLD key", q["app1_corr"], ref_corr))
    print(summary("app, app bright cal   vs OLD key", q["app2_corr"], ref_corr))
    print(summary("app, her spVar        vs NEW key", q["app1_corr"], key_corr))
    print(summary("app, app bright cal   vs NEW key _sp600", q["app2_corr"], key_corr_600))
    print(summary("app raw κ²            vs NEW key raw", q["app1_raw"], q["raw_lm"]))
    print(f"app <I>               vs NEW key meanVec: max |diff| {np.abs(q['app1_I'] - q['I']).max():.3g} DU")

    if args.write_key:
        if not exact:
            print(f"\nNOT writing {KEY_FILE.name}: the rebuild of her file is not exact (see a.)")
            return 1
        assert HER_FILE not in (KEY_FILE, REPO_KEY_FILE)
        key = {
            "corrSpeckleContrast": key_corr,
            "corrSpeckleContrast_sp600": key_corr_600,
            "rawSpeckleContrast": q["raw_lm"],
            "meanVec": q["I"],
            "G": G_app,
            "G_her_2024": G_her,
            "windowSize": float(W),
            "provenance": PROVENANCE,
        }
        for path in (REPO_KEY_FILE, KEY_FILE):
            path.parent.mkdir(exist_ok=True)
            scipy.io.savemat(path, key, do_compression=False)
            print(f"\nnew answer key -> {path}")
    print(f"done in {time.time()-t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
