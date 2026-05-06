"""
Offline SCOS analysis from a folder of per-frame TIFF files.

Replicates the full MATLAB pipeline (RecordSCOSLong.m) including:
  - Dark calibration  (from a _dark subfolder)
  - Bright calibration (spVar, computed from the mean of the main frames)
  - Per-frame corrected k2 and rBFi calculation
  - Optional comparison against MATLAB's LocalStd7x7_corr.mat

Usage (with the lab dataset):
    python tools/run_offline.py \\
        --data "C:/Users/USER/Scos Frames and results/expT5ms_Gain24dB_BL100DU_FR40Hz_005"

The dark folder is auto-detected as <data>_dark (then looks for a nested
subfolder with the same name, which is how pylon saves multi-session dark
recordings).

Camera note - Basler a2A1920-160umPRO:
    The camera is 10-bit but stores TIFFs as uint16 left-justified (x64 scale).
    Pixel values therefore range ~0-65472 (step = 64).
    To match MATLAB (which reads raw uint16), we work in the stored-DU domain
    and use bit_depth=16 when computing G = DU/e.
    This makes G = (2^16 / sat_capacity) * 10^(gain_dB/20) ~= 99.9 DU/e at 24 dB.
    The 1/12 quantization term is also kept as-is to match MATLAB literally.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import scipy.io
import tifffile
from scipy.ndimage import uniform_filter

sys.path.insert(0, str(Path(__file__).parent.parent))
from processor import convert_gain, local_variance, SCOSProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sorted_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    return sorted(files, key=lambda p: p.name)


def stream_mean_var(tiff_files: list[Path], scale: float, desc: str) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute per-pixel mean and unbiased variance (ddof=1) without loading
    the full stack into memory (Welford's online algorithm).

    Divides each frame by `scale` before accumulating so results are in
    native ADC units (e.g. 10-bit range after dividing stored uint16 by 64).
    Returns (mean, var) in scaled DU.
    """
    n = len(tiff_files)
    print(f"  {desc}: streaming {n} frames for mean/var ...")
    mean: np.ndarray | None = None
    M2:   np.ndarray | None = None
    for i, f in enumerate(tiff_files):
        frame = tifffile.imread(str(f)).astype(np.float64) / scale
        if mean is None:
            mean = np.zeros_like(frame)
            M2   = np.zeros_like(frame)
        count  = i + 1
        delta  = frame - mean
        mean  += delta / count
        delta2 = frame - mean
        M2    += delta * delta2
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{n}")
    assert mean is not None and M2 is not None
    return mean, M2 / (n - 1)   # unbiased variance, matches MATLAB var()


def stream_mean(tiff_files: list[Path], scale: float, desc: str) -> np.ndarray:
    """Compute per-pixel mean without loading the full stack."""
    n = len(tiff_files)
    print(f"  {desc}: streaming {n} frames for mean ...")
    acc: np.ndarray | None = None
    for i, f in enumerate(tiff_files):
        frame = tifffile.imread(str(f)).astype(np.float64) / scale
        acc = frame if acc is None else acc + frame
        if (i + 1) % 100 == 0:
            print(f"    {i+1}/{n}")
    assert acc is not None
    return acc / n


def load_mask(data_dir: Path, frame_shape: tuple) -> np.ndarray:
    mask_file = data_dir / "Mask.mat"
    if mask_file.exists():
        mat = scipy.io.loadmat(str(mask_file))
        if "totMask" in mat:
            mask = mat["totMask"].astype(bool)
            print(f"  Mask loaded from Mask.mat: {mask.shape}, {mask.sum()} ROI pixels")
            return mask
        keys = [k for k in mat if not k.startswith("_")]
        print(f"  Mask.mat found but no 'totMask'. Keys: {keys}")
    print(f"  No Mask.mat -- using full image ({frame_shape[0]}x{frame_shape[1]})")
    return np.ones(frame_shape, dtype=bool)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--data",         required=True,         help="Main recording folder (per-frame TIFFs)")
    p.add_argument("--dark",                                help="Dark calibration folder (auto-detected if omitted)")
    p.add_argument("--window",       type=int,   default=7,      help="Sliding window size (default 7)")
    p.add_argument("--gain-db",      type=float, default=24.0,   help="Camera gain in dB (default 24)")
    p.add_argument("--bit-depth",    type=int,   default=10,     help="Native ADC bit depth (default 10 for a2A1920)")
    p.add_argument("--sat-capacity", type=float, default=10400., help="Saturation capacity in e (default 10400 for a2A1920)")
    p.add_argument("--scale",        type=int,   default=64,     help="Divide stored TIFF values by this factor (default 64: 10-bit left-justified in uint16)")
    p.add_argument("--frame-rate",   type=float, default=40.0,   help="Frame rate in Hz (default 40)")
    p.add_argument("--out",                                help="Output .npz path (optional)")
    args = p.parse_args()

    data_dir = Path(args.data)
    if not data_dir.exists():
        print(f"ERROR: data folder not found: {data_dir}")
        sys.exit(1)

    # --- Auto-detect dark folder ---
    dark_dir: Path | None = Path(args.dark) if args.dark else None
    if dark_dir is None:
        candidate = data_dir.parent / (data_dir.name + "_dark")
        if candidate.exists():
            nested = candidate / candidate.name
            dark_dir = nested if nested.exists() else candidate
            print(f"Dark folder (auto-detected): {dark_dir}")

    # --- Discover TIFFs ---
    main_tiffs = sorted_tiffs(data_dir)
    if not main_tiffs:
        print(f"ERROR: no .tiff/.tif files found in {data_dir}")
        sys.exit(1)

    # --- Probe first frame ---
    sample = tifffile.imread(str(main_tiffs[0]))
    print(f"\nMain frames : {len(main_tiffs)}")
    print(f"Frame shape : {sample.shape}  dtype={sample.dtype}")
    print(f"First frame : min={sample.min()}  max={sample.max()}")

    scale = float(args.scale)
    print(f"Scale factor: /{scale}  (effective range after scaling: "
          f"[{sample.min()/scale:.1f}, {sample.max()/scale:.1f}] DU)")

    G = convert_gain(args.gain_db, args.bit_depth, args.sat_capacity)
    print(f"G = convert_gain({args.gain_db} dB, bit_depth={args.bit_depth}, "
          f"sat={args.sat_capacity}) = {G:.4f} DU/e")

    # --- Load mask ---
    print("\n--- Mask ---")
    mask = load_mask(data_dir, sample.shape)

    # --- Dark calibration ---
    proc = SCOSProcessor(
        window_size  = args.window,
        gain_db      = args.gain_db,
        bit_depth    = args.bit_depth,
        sat_capacity = args.sat_capacity,
    )

    print("\n--- Dark calibration ---")
    if dark_dir is not None:
        dark_tiffs = sorted_tiffs(dark_dir)
        print(f"Dark frames : {len(dark_tiffs)}")
        dark_mean, dark_var_raw = stream_mean_var(dark_tiffs, scale, "dark")
        proc.dark_mean = dark_mean
        proc.dark_var  = uniform_filter(dark_var_raw, size=args.window)
        print(f"  dark_mean (ROI) : {dark_mean[mask].mean():.2f} DU")
        print(f"  dark_var  (ROI) : {proc.dark_var[mask].mean():.4f} DU^2")
    else:
        print("  No dark folder found -- skipping dark calibration.")

    # --- Bright calibration from main frames ---
    # Matches MATLAB lines 273-274:
    #   spIm  = mean(main_frames) - darkIm
    #   spVar = stdfilt(spIm, true(windowSize)) .^ 2
    print("\n--- Bright calibration (from main frames) ---")
    bright_mean = stream_mean(main_tiffs, scale, "bright")
    sp_im = bright_mean - (proc.dark_mean if proc.dark_mean is not None else 0.0)
    _, sp_var = local_variance(sp_im, args.window)
    proc.bright_var = sp_var
    print(f"  spIm  mean (ROI): {sp_im[mask].mean():.2f} DU")
    print(f"  spVar mean (ROI): {sp_var[mask].mean():.6f} DU^2")

    # --- Per-frame processing ---
    print(f"\n--- Processing {len(main_tiffs)} frames ---")
    k2_raw  = np.empty(len(main_tiffs))
    k2_corr = np.empty(len(main_tiffs))
    mean_i  = np.empty(len(main_tiffs))

    for i, f in enumerate(main_tiffs):
        frame = (tifffile.imread(str(f)).astype(np.float64) / scale).astype(np.float32)
        k2_raw[i], k2_corr[i], mean_i[i] = proc.process(frame, mask)
        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(main_tiffs)}]  "
                  f"raw={k2_raw[i]:.5f}  corr={k2_corr[i]:.5f}  <I>={mean_i[i]:.1f} DU")

    time_vec = np.arange(len(k2_raw)) / args.frame_rate

    # rBFi: normalize by mean of first 10 s (MATLAB: first 10*frameRate samples)
    n_norm = min(round(10 * args.frame_rate), len(k2_corr))
    bfi    = 1.0 / k2_corr
    rbfi   = bfi / np.nanmean(bfi[:n_norm])

    # --- Compare with MATLAB ---
    matlab_file = data_dir / "LocalStd7x7_corr.mat"
    ml: dict = {}
    if matlab_file.exists():
        print(f"\n--- Comparison with MATLAB ({matlab_file.name}) ---")
        ml = scipy.io.loadmat(str(matlab_file))
        ml_keys = [k for k in ml if not k.startswith("_")]
        print(f"  Keys: {ml_keys}")

        for py_arr, ml_key, label in [
            (k2_corr, "corrSpeckleContrast", "Corrected k2"),
            (k2_raw,  "rawSpeckleContrast",  "Raw k2      "),
        ]:
            if ml_key not in ml:
                continue
            ml_raw_entry = ml[ml_key]
            # MATLAB cell arrays load as object ndarrays; extract the inner float array
            if ml_raw_entry.dtype == object:
                ml_val = ml_raw_entry.ravel()[0].ravel().astype(np.float64)
            else:
                ml_val = ml_raw_entry.ravel().astype(np.float64)
            print(f"  MATLAB {ml_key}: shape={ml_val.shape}  "
                  f"mean={ml_val.mean():.5f}  min={ml_val.min():.5f}  max={ml_val.max():.5f}")
            n   = min(len(py_arr), len(ml_val))
            err = np.abs(py_arr[:n] - ml_val[:n])
            ref = np.abs(ml_val[:n]) + 1e-10
            rel = err / ref
            print(f"  {label}:  max_abs={float(err.max()):.6f}  "
                  f"mean_rel={float(rel.mean())*100:.3f}%  max_rel={float(rel.max())*100:.3f}%")

    # --- Summary ---
    print(f"\n--- Summary ---")
    print(f"  Frames processed  : {len(k2_raw)}")
    print(f"  Raw k2            : mean={k2_raw.mean():.5f}  std={k2_raw.std():.5f}")
    print(f"  Corrected k2      : mean={k2_corr.mean():.5f}  std={k2_corr.std():.5f}")
    print(f"  Mean intensity    : {mean_i.mean():.1f} DU")
    print(f"  rBFi              : mean={rbfi.mean():.3f}  std={rbfi.std():.3f}")

    # --- Save ---
    if args.out:
        np.savez(
            args.out,
            time_vec=time_vec, k2_raw=k2_raw, k2_corr=k2_corr,
            mean_i=mean_i, rbfi=rbfi,
        )
        print(f"\nSaved: {args.out}")

    # --- Plot ---
    try:
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True)

        def extract_ml_array(key: str) -> np.ndarray | None:
            if key not in ml:
                return None
            v = ml[key]
            if v.dtype == object:
                v = v.ravel()[0]
            return v.ravel().astype(np.float64)

        axes[0].plot(time_vec, k2_raw, lw=0.8, label="Python")
        ml_raw_arr = extract_ml_array("rawSpeckleContrast")
        if ml_raw_arr is not None:
            axes[0].plot(time_vec[:len(ml_raw_arr)], ml_raw_arr[:len(time_vec)],
                         "--", lw=0.8, alpha=0.7, label="MATLAB")
        axes[0].set_ylabel("k2 raw"); axes[0].legend(); axes[0].grid(True)

        axes[1].plot(time_vec, k2_corr, lw=0.8, label="Python")
        ml_corr_arr = extract_ml_array("corrSpeckleContrast")
        if ml_corr_arr is not None:
            axes[1].plot(time_vec[:len(ml_corr_arr)], ml_corr_arr[:len(time_vec)],
                         "--", lw=0.8, alpha=0.7, label="MATLAB")
        axes[1].set_ylabel("k2 corrected"); axes[1].legend(); axes[1].grid(True)

        axes[2].plot(time_vec, mean_i, lw=0.8)
        axes[2].set_ylabel("<I> [DU]"); axes[2].set_xlabel("Time [s]"); axes[2].grid(True)

        plt.suptitle(f"SCOS offline: {data_dir.name}", fontsize=10)
        plt.tight_layout()

        out_png = data_dir / "offline_result.png"
        plt.savefig(str(out_png), dpi=150)
        print(f"Plot saved: {out_png}")
        plt.show()
    except ImportError:
        print("matplotlib not available -- skipping plot")


if __name__ == "__main__":
    main()
