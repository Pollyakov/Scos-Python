"""
Compare the app's κ² with Vika's MATLAB script, frame by frame, on frames the
app saved (docs/Plan_Frame_Saving.md, step 5).

First run her script on the session's saved measurement folder — it finds the
`_dark` folder next to it by itself:

    [~,~,~,~,~,info] = SCOSvsTime_WithNoiseSubtraction_Ver2( ...
        '<session>\\Frames\\expT8ms_Gain8dB_FR20Hz_BL100DU', [], 7, false, mask);

It writes `LocalStd7x7_corr.mat` into that folder. Then:

    venv\\Scripts\\python.exe tools/compare_with_matlab.py "<session folder>"

Frames are matched by file name: the script's `frameNames` against the app's
`Frames/frame_times.csv`, which gives each file's `timeVec` value, against the
results file. Prints how far apart raw κ², corrected κ² and ⟨I⟩ are.

What can differ, and why (read from her code, 2026-10-20): her `spVar` comes
from the first 600 frames of the recording itself, the app's from the bright
calibration frames; her ⟨I⟩² in the κ² denominator is a fitted mean image
(`FitMeanIm`), the app's the local mean. On the lab recording, with the same
dark frames, G and spVar, the two agree within 0.45 % (raw) and 1.2 %
(corrected) — CLAUDE.md.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import h5py
import numpy as np
import scipy.io

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from core.frame_writer import FRAME_TIMES_FILENAME, FRAMES_DIRNAME  # noqa: E402
from core.recorder import RESULTS_FILENAME                            # noqa: E402


def _cell(mat: dict, key: str) -> np.ndarray:
    """A MATLAB 1×1 cell of a vector, or a plain vector, as a 1-D array."""
    v = mat[key]
    while isinstance(v, np.ndarray) and v.dtype == object:
        v = v.ravel()[0]
    return np.asarray(v, dtype=np.float64).ravel()


def load_matlab(path: Path) -> dict:
    mat = scipy.io.loadmat(str(path))
    names = [str(np.asarray(e["name"]).ravel()[0]) for e in mat["frameNames"].ravel()]
    return {"raw": _cell(mat, "rawSpeckleContrast"),
            "corr": _cell(mat, "corrSpeckleContrast"),
            "mean": _cell(mat, "meanVec"),
            "names": names}


def load_app(session: Path) -> dict:
    with h5py.File(session / RESULTS_FILENAME, "r") as f:
        out = {k: f[k][:] for k in ("timeVec", "k2_raw", "k2_corr", "Intensity")}
        out["window"] = int(f["Params"].attrs["windowSize"])
        out["frames_folder"] = str(f["metadata"].attrs.get("frames_folder", ""))
    return out


def match(app: dict, mlab: dict, session: Path) -> tuple[np.ndarray, np.ndarray]:
    """Indices (into the app's series, into MATLAB's) of the same frames."""
    with open(session / FRAMES_DIRNAME / FRAME_TIMES_FILENAME, newline="") as fh:
        t_of = {Path(r["file"]).name: float(r["timeVec_s"])
                for r in csv.DictReader(fh) if r["phase"] == "measurement"}
    row_of = {round(float(t), 6): i for i, t in enumerate(app["timeVec"])}
    ia, im = [], []
    for j, name in enumerate(mlab["names"]):
        t = t_of.get(name)
        i = row_of.get(round(t, 6)) if t is not None else None
        if i is not None:
            ia.append(i)
            im.append(j)
    return np.array(ia, dtype=int), np.array(im, dtype=int)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("session", type=Path, help="the session folder (with Frames/)")
    ap.add_argument("--matlab-mat", type=Path, default=None,
                    help="her LocalStd<w>x<w>_corr.mat (default: in the saved "
                         "measurement folder)")
    args = ap.parse_args()

    app = load_app(args.session)
    mat_path = args.matlab_mat or (args.session / app["frames_folder"]
                                   / f"LocalStd{app['window']}x{app['window']}_corr.mat")
    if not mat_path.exists():
        print(f"not found: {mat_path} — run her script on the saved folder first")
        return 2
    mlab = load_matlab(mat_path)
    ia, im = match(app, mlab, args.session)
    print(f"session : {args.session}")
    print(f"MATLAB  : {mat_path}")
    print(f"frames  : app {len(app['timeVec'])}, MATLAB {len(mlab['names'])}, "
          f"matched {len(ia)}")
    if not len(ia):
        return 1
    in_order = bool(np.all(np.diff(ia) > 0))
    print(f"order   : {'the same' if in_order else 'DIFFERENT'} in both")

    rows = (("raw κ²", app["k2_raw"][ia], mlab["raw"][im]),
            ("corrected κ²", app["k2_corr"][ia], mlab["corr"][im]),
            ("⟨I⟩ [DU]", app["Intensity"][ia], mlab["mean"][im]))
    print(f"\n{'':14s} {'app mean':>12s} {'MATLAB mean':>12s} "
          f"{'mean |diff|':>12s} {'max |diff|':>11s}   (relative, per frame)")
    for label, a, m in rows:
        rel = np.abs(a - m) / np.abs(m)
        print(f"{label:14s} {a.mean():12.6g} {m.mean():12.6g} "
              f"{100 * rel.mean():11.2f} % {100 * rel.max():10.2f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
