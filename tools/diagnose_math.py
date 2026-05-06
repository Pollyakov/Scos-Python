"""
Phase-0 diagnostic: figure out what formula MATLAB actually uses for corrected κ².

Loads frame 0 (and optionally more frames), tries every candidate formula,
and compares against corrSpeckleContrast[N] from LocalStd7x7_corr.mat.

Usage:
    python tools/diagnose_math.py \
        --data "C:/Users/USER/Scos Frames and results/expT5ms_Gain24dB_BL100DU_FR40Hz_005"
"""

import argparse
import sys
import re
from pathlib import Path

import numpy as np
import scipy.io
import tifffile
from scipy.ndimage import uniform_filter

sys.path.insert(0, str(Path(__file__).parent.parent))
from processor import convert_gain, local_variance


SCALE   = 64.0    # 10-bit left-justified in uint16
WINDOW  = 7
GAIN_DB = 24.0
BIT_DEPTH = 10
SAT_CAP   = 10400.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sorted_tiffs(folder: Path) -> list[Path]:
    files = list(folder.glob("*.tiff")) + list(folder.glob("*.tif"))
    def idx(p: Path) -> int:
        m = re.search(r"_(\d{4})\.\w+$", p.name)
        return int(m.group(1)) if m else 0
    return sorted(files, key=idx)


def find_dark_dir(data_dir: Path) -> Path | None:
    candidate = data_dir.parent / (data_dir.name + "_dark")
    if not candidate.exists():
        return None
    nested = candidate / candidate.name
    return nested if (nested.exists() and nested.is_dir()) else candidate


def stream_mean_var_welford(tiff_files: list[Path], scale: float, label: str):
    """Per-pixel mean and unbiased variance (Welford, ddof=1). Returns (mean, var)."""
    n = len(tiff_files)
    print(f"  Streaming {n} {label} frames …")
    mean = M2 = None
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
    return mean, M2 / (n - 1)


def stream_mean(tiff_files: list[Path], scale: float, label: str) -> np.ndarray:
    n = len(tiff_files)
    print(f"  Streaming {n} {label} frames for mean …")
    acc = None
    for f in tiff_files:
        frame = tifffile.imread(str(f)).astype(np.float64) / scale
        acc = frame if acc is None else acc + frame
    return acc / n


def load_frame(path: Path, scale: float) -> np.ndarray:
    return tifffile.imread(str(path)).astype(np.float64) / scale


def rel_err(py_val: float, ml_val: float) -> float:
    return abs(py_val - ml_val) / (abs(ml_val) + 1e-10)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--n-frames", type=int, default=10,
                    help="Number of frames to compare (default 10)")
    args = ap.parse_args()

    data_dir = Path(args.data)
    dark_dir = find_dark_dir(data_dir)
    print(f"Recording: {data_dir}")
    print(f"Dark dir : {dark_dir}\n")

    # -----------------------------------------------------------------------
    # 1. TIFF file lists
    # -----------------------------------------------------------------------
    main_tiffs = sorted_tiffs(data_dir)
    dark_tiffs = sorted_tiffs(dark_dir) if dark_dir else []
    print(f"Main frames : {len(main_tiffs)}")
    print(f"Dark frames : {len(dark_tiffs)}\n")

    sample = tifffile.imread(str(main_tiffs[0]))
    print(f"Frame shape : {sample.shape}  dtype={sample.dtype}")
    print(f"Raw min/max : {sample.min()} / {sample.max()}")
    print(f"Divisible by {int(SCALE)}: {bool((sample % int(SCALE) == 0).all())}")

    # -----------------------------------------------------------------------
    # 2. Load MATLAB reference
    # -----------------------------------------------------------------------
    ref_mat = scipy.io.loadmat(str(data_dir / "LocalStd7x7_corr.mat"))

    def extract(key):
        v = ref_mat[key]
        if v.dtype == object:
            v = v.ravel()[0]
        return v.ravel().astype(np.float64)

    ml_raw  = extract("rawSpeckleContrast")
    ml_corr = extract("corrSpeckleContrast")
    ml_mean = extract("meanVec")
    print(f"\nMATLAB reference ({len(ml_corr)} frames):")
    print(f"  rawSpeckleContrast   mean={ml_raw.mean():.5f}  min={ml_raw.min():.5f}  max={ml_raw.max():.5f}")
    print(f"  corrSpeckleContrast  mean={ml_corr.mean():.5f}  min={ml_corr.min():.5f}  max={ml_corr.max():.5f}")
    print(f"  meanVec              mean={ml_mean.mean():.3f}  min={ml_mean.min():.3f}  max={ml_mean.max():.3f}")

    # -----------------------------------------------------------------------
    # 3. Load mask
    # -----------------------------------------------------------------------
    mask_mat = scipy.io.loadmat(str(data_dir / "Mask.mat"))
    mask = mask_mat["totMask"].astype(bool)
    print(f"\nMask: {mask.shape}  ROI pixels={mask.sum()}")

    # -----------------------------------------------------------------------
    # 4. Load smoothingCoefficients.mat
    # -----------------------------------------------------------------------
    sm_path = data_dir / "smoothingCoefficients.mat"
    sm = scipy.io.loadmat(str(sm_path))
    sp_im_mat = sm["spIm"].astype(np.float64)   # mean bright image (dark-subtracted?) from MATLAB
    sp_var_mat = sm["spVar"].astype(np.float64)  # something about bright variance
    fit_A = sm["fitI_A"].astype(np.float64)
    fit_B = sm["fitI_B"].astype(np.float64)

    print(f"\nsmoothingCoefficients.mat:")
    print(f"  spIm  : shape={sp_im_mat.shape}  min={sp_im_mat.min():.3f}  max={sp_im_mat.max():.3f}  mean(ROI)={sp_im_mat[mask].mean():.3f}")
    print(f"  spVar : shape={sp_var_mat.shape}  min={sp_var_mat.min():.6f}  max={sp_var_mat.max():.3f}  mean(ROI)={sp_var_mat[mask].mean():.6f}")
    print(f"  fitI_A: shape={fit_A.shape}  min={fit_A.min():.4f}  max={fit_A.max():.4f}  mean(ROI)={fit_A[mask].mean():.4f}")
    print(f"  fitI_B: shape={fit_B.shape}  min={fit_B.min():.3f}  max={fit_B.max():.3f}  mean(ROI)={fit_B[mask].mean():.3f}")

    # -----------------------------------------------------------------------
    # 5. Unit / interpretation checks
    # -----------------------------------------------------------------------
    print("\n--- Unit checks ---")

    # Does fitI_A * spIm + fitI_B ≈ spVar?
    ptc_predicted = fit_A * sp_im_mat + fit_B
    residual = ptc_predicted - sp_var_mat
    print(f"fitI_A * spIm + fitI_B vs spVar (ROI):")
    print(f"  predicted mean(ROI)={ptc_predicted[mask].mean():.6f}  actual={sp_var_mat[mask].mean():.6f}")
    print(f"  residual  mean(ROI)={residual[mask].mean():.6f}  std={residual[mask].std():.6f}")
    check_ptc = abs(residual[mask].mean()) < 0.01 * abs(sp_var_mat[mask].mean() + 1e-10)
    print(f"  => fitI_A*spIm+fitI_B ~= spVar? {'YES' if check_ptc else 'NO'}")

    # G from table
    G_table = convert_gain(GAIN_DB, BIT_DEPTH, SAT_CAP)
    print(f"\nG_table = convert_gain({GAIN_DB}, {BIT_DEPTH}, {SAT_CAP}) = {G_table:.5f} DU/e")
    print(f"fitI_A mean(ROI) = {fit_A[mask].mean():.5f} DU/e")
    print(f"Ratio fitI_A/G_table = {fit_A[mask].mean()/G_table:.4f}")

    # -----------------------------------------------------------------------
    # 6. Dark calibration
    # -----------------------------------------------------------------------
    print("\n--- Dark calibration ---")
    if dark_tiffs:
        dark_mean, dark_var_raw = stream_mean_var_welford(dark_tiffs, SCALE, "dark")
        dark_var = uniform_filter(dark_var_raw, size=WINDOW)
        print(f"  dark_mean (ROI) : {dark_mean[mask].mean():.4f} DU")
        print(f"  dark_var  (ROI) : {dark_var[mask].mean():.6f} DU² (spatially smoothed)")
        print(f"  dark_var  (ROI) : {dark_var_raw[mask].mean():.6f} DU² (un-smoothed)")
    else:
        dark_mean = np.zeros(sample.shape, dtype=np.float64)
        dark_var  = np.zeros(sample.shape, dtype=np.float64)
        print("  No dark frames — using zeros.")

    # -----------------------------------------------------------------------
    # 7. Bright calibration (Python style: from main frames)
    # -----------------------------------------------------------------------
    print("\n--- Python-style bright calibration (mean of main frames) ---")
    bright_mean = stream_mean(main_tiffs, SCALE, "main")
    sp_im_py = bright_mean - dark_mean
    _, sp_var_py = local_variance(sp_im_py, WINDOW)
    print(f"  spIm_py  mean(ROI) = {sp_im_py[mask].mean():.4f} DU")
    print(f"  spVar_py mean(ROI) = {sp_var_py[mask].mean():.6f} DU²")

    # Compare Python spIm vs MATLAB spIm
    print(f"\n  MATLAB spIm  mean(ROI) = {sp_im_mat[mask].mean():.4f} DU")
    print(f"  Python spIm  mean(ROI) = {sp_im_py[mask].mean():.4f} DU")
    print(f"  Ratio: {sp_im_mat[mask].mean() / (sp_im_py[mask].mean() + 1e-10):.4f}")
    print(f"  spIm_py * 64 mean = {(sp_im_py * 64)[mask].mean():.2f}  (to check if MATLAB is in raw uint16)")

    print(f"\n  MATLAB spVar mean(ROI) = {sp_var_mat[mask].mean():.6f} DU²")
    print(f"  Python spVar mean(ROI) = {sp_var_py[mask].mean():.6f} DU²")

    # -----------------------------------------------------------------------
    # 8. Per-frame comparison — all candidates
    # -----------------------------------------------------------------------
    print(f"\n--- Per-frame comparison (first {args.n_frames} frames) ---")

    n = min(args.n_frames, len(main_tiffs), len(ml_corr))

    results = {
        "A_current":       [],   # G_table*mean + spVar_py + dark_var  (current Python)
        "B_ptc_darkvar":   [],   # fitI_A*mean + fitI_B + dark_var
        "C_ptc_nodark":    [],   # fitI_A*mean + fitI_B  (no dark_var)
        "D_spvarmat":      [],   # G_table*mean + spVar_mat + dark_var
        "E_noshot_spvarpy":[], # spVar_py + dark_var  (no shot noise)
        "F_nodarkvar":     [],   # G_table*mean + spVar_py  (no dark_var)
        "raw":             [],
    }

    for i in range(n):
        frame = load_frame(main_tiffs[i], SCALE)
        im = frame - dark_mean
        mean_im, var_im = local_variance(im, WINDOW)
        mean_sq = mean_im ** 2
        safe = mean_sq > 0

        # Raw k2
        k2_raw_map = np.where(safe, var_im / mean_sq, np.nan)
        k2_raw = float(np.nanmean(k2_raw_map[mask]))
        results["raw"].append(k2_raw)

        def k2_corr(noise_map):
            corr = var_im - noise_map - 1.0 / 12.0
            cmap = np.where(safe, corr / mean_sq, np.nan)
            return float(np.nanmean(cmap[mask]))

        results["A_current"].append(k2_corr(G_table * mean_im + sp_var_py + dark_var))
        results["B_ptc_darkvar"].append(k2_corr(fit_A * mean_im + fit_B + dark_var))
        results["C_ptc_nodark"].append(k2_corr(fit_A * mean_im + fit_B))
        results["D_spvarmat"].append(k2_corr(G_table * mean_im + sp_var_mat + dark_var))
        results["E_noshot_spvarpy"].append(k2_corr(sp_var_py + dark_var))
        results["F_nodarkvar"].append(k2_corr(G_table * mean_im + sp_var_py))

    ml_slice = ml_corr[:n]

    print(f"\n{'Candidate':<22} {'mean_val':>10} {'MATLAB_mean':>12} {'mean_rel%':>10} {'max_rel%':>10}")
    print("-" * 68)
    for key, vals in results.items():
        v = np.array(vals)
        ml = ml_slice if key != "raw" else ml_raw[:n]
        rel = np.abs(v - ml) / (np.abs(ml) + 1e-10) * 100
        print(f"  {key:<20} {v.mean():>10.6f} {ml.mean():>12.6f} {rel.mean():>9.2f}% {rel.max():>9.2f}%")

    # -----------------------------------------------------------------------
    # 8b. Solve for exact G assuming Python dark_var + spVar_mat is correct
    # -----------------------------------------------------------------------
    print("\n--- Solving for exact G (assuming dark_var and spVar_mat are correct) ---")
    # K²_corr = (var - G*mean - spVar_mat - dark_var - 1/12) / mean²
    # G = (var - K²_corr*mean² - spVar_mat - dark_var - 1/12) / mean
    g_solved = []
    for i in range(n):
        frame_i = load_frame(main_tiffs[i], SCALE)
        im_i = frame_i - dark_mean
        mean_i, var_i = local_variance(im_i, WINDOW)
        mean_roi_i = float(np.mean(mean_i[mask]))
        var_roi_i  = float(np.mean(var_i[mask]))
        num_i = var_roi_i - ml_corr[i] * mean_roi_i**2 - float(np.mean(sp_var_mat[mask])) - float(np.mean(dark_var[mask])) - 1/12
        g_i = num_i / mean_roi_i
        g_solved.append(g_i)
    print(f"  Solved G (using dark_var_py + spVar_mat): mean={np.mean(g_solved):.5f}  std={np.std(g_solved):.5f}")

    # Also solve assuming no dark_var in MATLAB formula
    g_nodark = []
    for i in range(n):
        frame_i = load_frame(main_tiffs[i], SCALE)
        im_i = frame_i - dark_mean
        mean_i, var_i = local_variance(im_i, WINDOW)
        mean_roi_i = float(np.mean(mean_i[mask]))
        var_roi_i  = float(np.mean(var_i[mask]))
        num_i = var_roi_i - ml_corr[i] * mean_roi_i**2 - float(np.mean(sp_var_mat[mask])) - 1/12
        g_i = num_i / mean_roi_i
        g_nodark.append(g_i)
    print(f"  Solved G (no dark_var, spVar_mat):        mean={np.mean(g_nodark):.5f}  std={np.std(g_nodark):.5f}")

    # Solve for dark_var factor assuming G_table is correct
    dv_factors = []
    dv_mean = float(np.mean(dark_var[mask]))
    sp_mean = float(np.mean(sp_var_mat[mask]))
    for i in range(n):
        frame_i = load_frame(main_tiffs[i], SCALE)
        im_i = frame_i - dark_mean
        mean_i, var_i = local_variance(im_i, WINDOW)
        mean_roi_i = float(np.mean(mean_i[mask]))
        var_roi_i  = float(np.mean(var_i[mask]))
        noise_known = G_table * mean_roi_i + sp_mean + 1/12
        dv_effective = var_roi_i - ml_corr[i] * mean_roi_i**2 - noise_known
        dv_factors.append(dv_effective / dv_mean if dv_mean > 0 else 0)
    print(f"  dark_var factor (assuming G_table, spVar_mat): mean={np.mean(dv_factors):.5f}  std={np.std(dv_factors):.5f}")

    # Add solved G candidates to results
    G_solved_val = float(np.mean(g_solved))
    G_nodark_val = float(np.mean(g_nodark))
    dv_factor    = float(np.mean(dv_factors))

    # Test: does fitI_A as per-pixel G modifier improve things?
    # shot_noise_spatial = fitI_A * G_table * mean_im  (per-pixel product)
    print(f"\n--- fitI_A as per-pixel G modifier ---")
    frame0 = load_frame(main_tiffs[0], SCALE)
    im0 = frame0 - dark_mean
    mean0, _ = local_variance(im0, WINDOW)
    spatial_product     = float(np.mean((fit_A * mean0)[mask]))
    scalar_product      = float(np.mean(fit_A[mask])) * float(np.mean(mean0[mask]))
    print(f"  mean(fitI_A * mean_im) frame 0  = {spatial_product:.5f} DU")
    print(f"  mean(fitI_A)*mean(mean_im)       = {scalar_product:.5f} DU  (ignoring spatial covariance)")
    print(f"  G_table * mean(mean_im)           = {G_table * float(np.mean(mean0[mask])):.5f} DU")
    print(f"  Effective G from spatial product  = {spatial_product / float(np.mean(mean0[mask])):.5f} DU/e")

    # What sat_capacity corresponds to G_solved?
    sat_cap_solved = (2**BIT_DEPTH) * (10**(GAIN_DB/20.0)) / G_solved_val
    print(f"\n--- Implied sat_capacity for G_solved={G_solved_val:.5f} ---")
    print(f"  sat_capacity = 2^{BIT_DEPTH} * 10^({GAIN_DB}/20) / {G_solved_val:.5f}")
    print(f"               = {sat_cap_solved:.1f} e-  (vs input {SAT_CAP} e-)")

    results["G_solved"]        = []
    results["G_nodark"]        = []
    results["dv_scaled"]       = []
    results["fitA_G_spatial"]  = []   # fitI_A * G_table * mean per pixel
    for i in range(n):
        frame_i = load_frame(main_tiffs[i], SCALE)
        im_i = frame_i - dark_mean
        mean_i, var_i = local_variance(im_i, WINDOW)
        mean_sq_i = mean_i ** 2
        safe_i = mean_sq_i > 0
        def k2c(noise_map):
            c = var_i - noise_map - 1/12
            return float(np.nanmean(np.where(safe_i, c / mean_sq_i, np.nan)[mask]))
        results["G_solved"].append(k2c(G_solved_val * mean_i + sp_var_mat + dark_var))
        results["G_nodark"].append(k2c(G_nodark_val * mean_i + sp_var_mat))
        results["dv_scaled"].append(k2c(G_table * mean_i + sp_var_mat + dv_factor * dark_var))
        results["fitA_G_spatial"].append(k2c(fit_A * G_table * mean_i + sp_var_mat + dark_var))

    # Identify best candidate
    best = min(
        (k for k in results if k != "raw"),
        key=lambda k: np.abs(np.array(results[k]) - ml_slice).mean()
    )
    print(f"\n  Best candidate: {best}")

    # -----------------------------------------------------------------------
    # 9. Frame-by-frame detail for best and second-best
    # -----------------------------------------------------------------------
    # Sort by mean relative error
    ranked = sorted(
        (k for k in results if k != "raw"),
        key=lambda k: np.abs(np.array(results[k]) - ml_slice).mean()
    )
    print(f"\n--- Ranked candidates (best first) ---")
    for rank, key in enumerate(ranked, 1):
        v = np.array(results[key])
        rel = np.abs(v - ml_slice) / (np.abs(ml_slice) + 1e-10) * 100
        print(f"  {rank}. {key:<22} mean_rel={rel.mean():.3f}%  max_rel={rel.max():.3f}%")

    print(f"\n--- Frame-by-frame: best two candidates ---")
    print(f"{'Frame':>6} {'MATLAB':>10} {'raw':>10}", end="")
    for key in ranked[:2]:
        print(f"  {key:>22}", end="")
    print()
    for i in range(n):
        print(f"  {i:>4}  {ml_slice[i]:>10.6f}  {results['raw'][i]:>10.6f}", end="")
        for key in ranked[:2]:
            diff = results[key][i] - ml_slice[i]
            print(f"  {results[key][i]:>10.6f} ({diff:+.6f})", end="")
        print()

    # -----------------------------------------------------------------------
    # 10. Summary of noise terms for one frame
    # -----------------------------------------------------------------------
    print("\n--- Noise budget for frame 0 (ROI means, in DU²) ---")
    frame0 = load_frame(main_tiffs[0], SCALE)
    im0 = frame0 - dark_mean
    mean0, var0 = local_variance(im0, WINDOW)

    var_roi     = float(np.mean(var0[mask]))
    mean_roi    = float(np.mean(mean0[mask]))
    shot_table  = G_table * mean_roi
    shot_ptc    = float(np.mean((fit_A * mean0)[mask]))
    fitB_roi    = float(np.mean(fit_B[mask]))
    spvar_py_roi = float(np.mean(sp_var_py[mask]))
    spvar_mat_roi = float(np.mean(sp_var_mat[mask]))
    dkvar_roi   = float(np.mean(dark_var[mask]))
    quant       = 1.0 / 12.0

    print(f"  mean(ROI)          = {mean_roi:.4f} DU")
    print(f"  var_raw(ROI)       = {var_roi:.4f} DU²")
    print(f"  G_table * mean     = {shot_table:.4f} DU²   (shot noise, table G={G_table:.4f})")
    print(f"  fitI_A * mean      = {shot_ptc:.4f} DU²   (shot noise, empirical fitI_A)")
    print(f"  fitI_B (ROI)       = {fitB_roi:.4f} DU²")
    print(f"  spVar_py (ROI)     = {spvar_py_roi:.6f} DU²  (Python spatial var of mean bright)")
    print(f"  spVar_mat (ROI)    = {spvar_mat_roi:.6f} DU²  (MATLAB spVar)")
    print(f"  dark_var (ROI)     = {dkvar_roi:.6f} DU²")
    print(f"  quant (1/12)       = {quant:.4f} DU²")
    print()
    for label, noise in [
        ("A_current  (G_table+spVar_py+dark)", shot_table + spvar_py_roi + dkvar_roi + quant),
        ("B_ptc+dark (fitA*m+fitB+dark)",      shot_ptc   + fitB_roi     + dkvar_roi + quant),
        ("C_ptc_nodark (fitA*m+fitB)",          shot_ptc   + fitB_roi                + quant),
        ("D_spvarmat (G+spVar_mat+dark)",       shot_table + spvar_mat_roi + dkvar_roi + quant),
    ]:
        k2 = (var_roi - noise) / (mean_roi ** 2)
        print(f"  {label:<40} total_noise={noise:.4f}  K²_corr={k2:.6f}")
    print(f"  MATLAB corrected K² (frame 0) = {ml_corr[0]:.6f}")


if __name__ == "__main__":
    main()
