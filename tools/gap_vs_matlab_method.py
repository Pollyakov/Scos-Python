"""Which of the three app-vs-MATLAB differences explains the corrected-kappa^2 gap?

Session scos_20261022_165057 (saved frames). Read-only. Factors:
  S  spVar source : app = bright calibration (Calibration.h5) | her = first 1000 measurement frames
  D  intensity    : app = local 7x7 mean of the frame          | her = FitMeanIm fitted image
  M  mask         : app = circle eroded by 4 px                | her = full circle, image border trimmed
Everything else identical (same dark mean, same dark var, same G, 1/12).

Written 2026-11-01 for the 8 % gap with Vika's MATLAB run (docs/todo.md, item A5); result there:
all three together +1.5 %. Her method is copied from SCOSvsTime_WithNoiseSubtraction_Ver2.m and
baseFunc/FitMeanIm.m. Paths are fixed to that session (S below) — edit them for another one.

    venv\\Scripts\\python.exe tools/gap_vs_matlab_method.py      (about 4 min; reads only)

Prints the 8 combinations, her first-frame console line, and writes gap_arrays.npz (per-frame
values) into the current folder. Not named *_test.py on purpose: pytest would collect it.
"""
import csv
import sys
from pathlib import Path

import h5py
import numpy as np
import tifffile
from scipy.ndimage import uniform_filter

REPO = Path(r"C:\Users\USER\Scos-Python")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
from processor import SCOSProcessor, shrink_mask_for_window  # noqa: E402

S = Path(r"C:\Users\USER\Desktop\scos gui testing\vika 08 10\TestKsenia\scos_20261022_165057")
FR = S / "Frames"
MEAS = FR / "expT8ms_Gain8dB_FR20Hz_BL100DU"
W = 7
BL = 100.0
N_SP = 1000          # her script: 600, but 1000 when the recording has > 1000 frames

with h5py.File(S / "rBFi_resultsAndParameters.h5", "r") as f:
    k2_raw_app = f["k2_raw"][:].ravel()
    k2_corr_app = f["k2_corr"][:].ravel()
    t_res = f["timeVec"][:].ravel()
    G = float(f["metadata"].attrs["gain_du_per_e"])
    params = dict(f["Params"].attrs)
with h5py.File(S / "Calibration.h5", "r") as f:
    mean_dark = f["dark/mean_dark"][:].astype(np.float64)
    var_dark = f["dark/var_dark"][:].astype(np.float64)
    spVar_app = f["bright/spVar"][:].astype(np.float64)
    circle = f["dark/mask"][:].astype(bool)
print(f"G = {G:.6f}, window = {params.get('windowSize')}, frame {circle.shape}, "
      f"circle px = {circle.sum()}")

# --- frames in index order; check the order equals file-time order (her sort) ---
files = sorted(MEAS.glob("*.tiff"))
mt = [p.stat().st_mtime_ns for p in files]
print(f"measurement files: {len(files)}; time order == name order: {mt == sorted(mt)}")
first = tifffile.imread(files[0])
print(f"dtype {first.dtype}, max {first.max()}, all multiples of 16: {bool(np.all(first % 16 == 0))}"
      " (her devide_by would be 16 if True)")

# --- pairing: file -> result index via frame_times.csv ---
idx_of_t = {round(float(t), 6): i for i, t in enumerate(t_res)}
pair = {}
with open(FR / "frame_times.csv", newline="") as fh:
    for r in csv.DictReader(fh):
        if r["phase"] == "measurement" and r["timeVec_s"]:
            k = round(float(r["timeVec_s"]), 6)
            if k in idx_of_t:
                pair[Path(r["file"]).name] = idx_of_t[k]
print(f"files with a result: {len(pair)} of {len(files)}; results: {len(t_res)}")

# --- masks ---
ws2 = int(np.ceil(W / 2))
her_mask = circle.copy()
her_mask[:ws2, :] = False; her_mask[-ws2:, :] = False
her_mask[:, :ws2] = False; her_mask[:, -ws2:] = False
app_mask = shrink_mask_for_window(circle, W)
print(f"mask px: app (eroded) {app_mask.sum()}, her (full circle) {her_mask.sum()}")

background = mean_dark - BL
print(f"background = dark mean - BL: ROI mean {background[her_mask].mean():+.4f} DU")


def local(im):
    """float64 local mean and stdfilt^2 (N-1)."""
    m = uniform_filter(im, W, mode="reflect")
    m2 = uniform_filter(im * im, W, mode="reflect")
    n = W * W
    return m, np.maximum(m2 - m * m, 0) * n / (n - 1)


# --- pass 1: her spVar and FitMeanIm over the first N_SP frames (raw - BL) ---
sum_rec = np.zeros_like(mean_dark)
sx = sxx = 0.0
sy = np.zeros_like(mean_dark)
sxy = np.zeros_like(mean_dark)
for p in files[:N_SP]:
    rec = tifffile.imread(p).astype(np.float64) - BL
    sum_rec += rec
    x = rec[her_mask].mean()
    y = uniform_filter(rec, W, mode="constant", cval=0.0)   # imfilter 'same', zero pad
    sx += x; sxx += x * x; sy += y; sxy += x * y
n = float(N_SP)
fitA = (n * sxy - sx * sy) / (n * sxx - sx * sx)
fitB = (sy - fitA * sx) / n
spIm_her = sum_rec / n - background
_, spVar_her = local(spIm_her)
print(f"spVar ROI mean: app (bright) {spVar_app[her_mask].mean():.4f}, "
      f"her (first {N_SP} meas) {spVar_her[her_mask].mean():.4f} DU^2")
print(f"fit A ROI mean {fitA[her_mask].mean():.4f}, B ROI mean {fitB[her_mask].mean():+.4f} DU")

# --- the app's own path, to anchor ---
proc = SCOSProcessor(window_size=W, gain_db=float(params["gain"]), bit_depth=12,
                     camera_sn="40513592")
proc.scale = 1.0
proc.dark_mean, proc.dark_var, proc.bright_var = mean_dark, var_dark, spVar_app
proc.set_roi(app_mask)

combos = [(s, d, m) for s in ("app", "her") for d in ("app", "her") for m in ("app", "her")]
out = {c: [] for c in combos}
out_raw = {c: [] for c in combos}
proc_corr, res_idx, diag = [], [], None
for p in files:
    if p.name not in pair:
        continue
    raw_u16 = tifffile.imread(p)
    _, c_proc, _ = proc.process(raw_u16, None)
    proc_corr.append(c_proc)
    res_idx.append(pair[p.name])
    im = raw_u16.astype(np.float64) - mean_dark          # = (raw-BL) - background
    mean_im, var_im = local(im)
    meanFrame = im[her_mask].mean()
    fittedI = fitA * meanFrame + fitB
    for (s, d, m) in combos:
        sp = spVar_app if s == "app" else spVar_her
        I = mean_im if d == "app" else fittedI
        mk = app_mask if m == "app" else her_mask
        I2 = I[mk] ** 2
        out_raw[(s, d, m)].append(np.mean(var_im[mk] / I2))
        out[(s, d, m)].append(np.mean((var_im[mk] - G * I[mk] - sp[mk] - 1 / 12
                                       - var_dark[mk]) / I2))
    if diag is None:   # her first-frame console line, her definitions, her corner
        mk = her_mask; I = fittedI[mk]; I2 = I ** 2
        diag = dict(I=meanFrame, K_raw=np.mean(var_im[mk] / I2), Ks=np.mean(G * I / I2),
                    Kr=float("nan"),   # filled in after the loop
                    Ksp=np.mean(spVar_her[mk] / I2), Kq=np.mean(1 / (12 * I2)),
                    Kf=out[("her", "her", "her")][0])
        # Kr: she prints darkVar (per pixel, not box-filtered) — rebuilt below

res_idx = np.array(res_idx)
proc_corr = np.array(proc_corr)
ref = k2_corr_app[res_idx]
print()
print(f"frames compared: {len(res_idx)}")
print(f"anchor 1: SCOSProcessor rerun vs results file, corrected mean: "
      f"{proc_corr.mean():.7f} vs {ref.mean():.7f} (stored all frames {k2_corr_app.mean():.7f})")
print(f"anchor 2: my float64 app corner vs results file: "
      f"{np.mean(out[('app','app','app')]):.7f}")
print()
print("spVar  intensity  mask  | raw k2 mean  vs app | corr k2 mean  vs app (means)  median per-frame")
for c in combos:
    raw = np.array(out_raw[c]); cor = np.array(out[c])
    dr = (raw.mean() / k2_raw_app[res_idx].mean() - 1) * 100
    dc = (cor.mean() / ref.mean() - 1) * 100
    med = np.median((cor / ref - 1) * 100)
    print(f"{c[0]:>5}  {c[1]:>9}  {c[2]:>4}  | {raw.mean():.6f}  {dr:+6.2f}% | "
          f"{cor.mean():.7f}  {dc:+6.2f}%        {med:+6.2f}%")

# her first-frame line; Kr as she prints it: darkVar per pixel (unfiltered) / fittedI^2.
# Per-pixel dark var is not in Calibration.h5 (box-filtered only), so rebuild from the dark files.
dk = sorted((FR / "expT8ms_Gain8dB_FR20Hz_BL100DU_dark").glob("*.tiff"))
acc = np.zeros_like(mean_dark); acc2 = np.zeros_like(mean_dark)
for p in dk:
    a = tifffile.imread(p).astype(np.float64); acc += a; acc2 += a * a
nd = len(dk)
dv_px = (acc2 - acc * acc / nd) / (nd - 1)
first_file = next(p for p in files if p.name in pair)
im = tifffile.imread(first_file).astype(np.float64) - mean_dark
fI = fitA * im[her_mask].mean() + fitB
diag["Kr"] = np.mean(dv_px[her_mask] / fI[her_mask] ** 2)
print()
print("her first-frame line (her corner): " +
      ", ".join(f"{k}={v:.5g}" for k, v in diag.items()))
print(f"box-filtered dark var (Calibration.h5) vs rebuilt: max diff "
      f"{np.abs(uniform_filter(dv_px, W) - var_dark)[her_mask].max():.2e}")

np.savez("gap_arrays.npz", res_idx=res_idx, app_corr=ref, app_raw=k2_raw_app[res_idx],
         her_corr=np.array(out[("her", "her", "her")]), her_raw=np.array(out_raw[("her", "her", "her")]))
