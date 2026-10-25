"""
Check the Frames folder of a session saved with Save Frames — before it goes to
Vika for her MATLAB check (docs/Plan_Frame_Saving.md, rig_checklist.md R8).

Prints each check with a verdict, as tools/check_session.py does:
    OK      the rule holds
    NOT OK  a hard rule is broken: copy the output and tell Claude
    CHECK   no hard rule (fine in some sessions): look at it
    -       information only

What it checks:
- the file counts against the results file's metadata and Calibration.h5;
- the layout her script reads: the black level in the folder name, 16-bit
  uncompressed TIFFs, file times rising with the frame number (she sorts by
  them), paths under Windows' 260-character limit;
- `frame_times.csv`: one row per file, a saved frame for every `timeVec` point;
- that the saved files are exactly the frames the math used: the dark and
  bright averages rebuilt from them against Calibration.h5, and κ² recomputed
  from every saved measurement frame against the results file.

It only reads. The recomputation takes about a minute for a 1-minute cut
session (longer on the full frame). Exit code: 0 = no NOT OK, 1 = at least one
NOT OK, 2 = not a session folder with saved frames.

Usage:
    venv\\Scripts\\python.exe tools/check_saved_frames.py "<session folder>"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import h5py
import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.frame_writer import FRAME_TIMES_FILENAME, MAX_PATH_CHARS
from core.recorder import CALIBRATION_FILENAME, RESULTS_FILENAME
from folder_camera import FolderMockCamera
from processor import SCOSProcessor, shrink_mask_for_window
from tools.check_session import CHECK, INFO, NOT_OK, OK, Report

PHASES = ("dark", "bright", "measurement")
# Frames still in the pipeline's queue at Stop SCOS are saved but get no result.
PIPELINE_QUEUE = 20
# Calibration.h5 stores the averages as float32: ≈ 1e-5 DU at the rig's levels.
CAL_TOLERANCE_DU = 1e-3
# The rerun is the same code on the same frames; anything above rounding is a
# different frame.
RERUN_TOLERANCE = 1e-6

_BL_TOKEN = re.compile(r"_BL([\d.]+)DU")


def _phase_dirs(frames: Path, name: str) -> dict[str, Path]:
    return {"dark": frames / f"{name}_dark", "bright": frames / f"{name}_bright",
            "measurement": frames / name}


def _mean_of(files: list[Path]) -> np.ndarray:
    """Per-pixel mean of the frames, summed one at a time (600 full frames as
    one float64 stack would be 11 GB)."""
    total = None
    for f in files:
        a = tifffile.imread(f).astype(np.float64)
        total = a if total is None else total + a
    return total / len(files)


def layout(rep: Report, folder: Path, res: dict, cal: dict) -> dict[str, list[Path]] | None:
    """Counts, names and file format. Returns the files of each phase."""
    print("== Frames")
    meta = res["meta"]
    rel = meta.get("frames_folder")
    if not rel:
        rep.line(NOT_OK, "no frames_folder in the results file's metadata - "
                         "the session was recorded without Save Frames")
        return None
    main = folder / str(rel)
    name = main.name
    rep.row(INFO, "folder", name)
    m = _BL_TOKEN.search(name)
    bl = meta.get("camera_black_level")
    if bl is None:
        rep.row(CHECK, "black level", f"name {m.group(1) if m else 'none'}",
                "the camera reported none to compare with")
    else:
        same = m is not None and float(m.group(1)) == float(bl)
        rep.row(OK if same else NOT_OK, "black level in name",
                m.group(1) if m else "none", f"camera reported {float(bl):g}; "
                "her script subtracts the name's value")

    files = {ph: sorted(d.glob("*.tiff")) if d.is_dir() else []
             for ph, d in _phase_dirs(main.parent, name).items()}
    for ph in PHASES:
        n, saved = len(files[ph]), meta.get(f"frames_saved_{ph}")
        not_saved = int(meta.get(f"frames_not_saved_{ph}", 0))
        rep.row(OK if saved is not None and n == int(saved) else NOT_OK,
                f"{ph} files", n, f"metadata says {saved}")
        if not_saved:
            rep.row(NOT_OK, f"{ph} not saved", not_saved,
                    "skipped while the disk fell behind: those frames are missing")
    for ph in ("dark", "bright"):
        used = cal.get(f"{ph}_n")
        if used is not None:
            rep.row(OK if len(files[ph]) == used else NOT_OK, f"{ph} = calibration",
                    f"{len(files[ph])} / {used}", "files / frames the calibration used")

    every = [f for fs in files.values() for f in fs]
    if not every:
        return files
    longest = max(len(str(f)) for f in every)
    rep.row(OK if longest <= MAX_PATH_CHARS else NOT_OK, "longest path", longest,
            f"Windows limit {MAX_PATH_CHARS}")
    sn = str(meta.get("camera_sn", ""))
    named = all(f.name.startswith("Basler_") and f"__{sn}__" in f.name for f in every)
    rep.row(OK if named else NOT_OK, "file names", every[0].name,
            "" if named else f"her script reads the SN ({sn}) between '__'")

    bits = int(res["params"].get("bitDepth", 12))
    for ph in PHASES:
        if not files[ph]:
            continue
        with tifffile.TiffFile(files[ph][0]) as t:
            page = t.pages[0]
            a = t.asarray()
        fine = page.compression.name == "NONE" and (
            a.dtype == np.uint8 if bits == 8 else a.dtype == np.uint16)
        rep.row(OK if fine else NOT_OK, f"{ph} format",
                f"{a.dtype}, compression {page.compression.name}, values "
                f"{int(a.min())}..{int(a.max())}")
        mt = np.array([f.stat().st_mtime for f in files[ph]])
        rising = len(mt) < 2 or bool(np.all(np.diff(mt) > 0))
        rep.row(OK if rising else NOT_OK, f"{ph} file times",
                "rise with the index" if rising else "NOT in frame order",
                "her script sorts by them")
    return files


def frame_times(rep: Report, folder: Path, res: dict, files: dict) -> list[dict]:
    """frame_times.csv against the files and timeVec. Returns the measurement
    rows that have a result, with the index of that result."""
    path = folder / "Frames" / FRAME_TIMES_FILENAME
    if not path.exists():
        rep.line(NOT_OK, f"no {FRAME_TIMES_FILENAME}")
        return []
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    n_files = sum(len(v) for v in files.values())
    rep.row(OK if len(rows) == n_files else NOT_OK, "frame_times rows", len(rows),
            f"{n_files} files")
    t_res = res["t"]
    index = {round(float(t), 6): i for i, t in enumerate(t_res)}
    meas = [r for r in rows if r["phase"] == "measurement"]
    # A row counts only if its file is really there.
    matched = [(r, index[round(float(r["timeVec_s"]), 6)]) for r in meas
               if r["timeVec_s"] and round(float(r["timeVec_s"]), 6) in index
               and (folder / "Frames" / r["file"]).exists()]
    rep.row(OK if len(matched) == len(t_res) else NOT_OK, "timeVec with a frame",
            f"{len(matched)} / {len(t_res)}")
    extra = len(meas) - len(matched)
    allowed = PIPELINE_QUEUE + int(res["meta"].get("frames_dropped_queue", 0))
    rep.row(OK if 0 <= extra <= allowed else CHECK, "frames without result", extra,
            f"still queued at Stop SCOS: up to {allowed}")
    return [{"file": r["file"], "i": i} for r, i in matched]


def same_frames(rep: Report, folder: Path, res: dict, cal: dict, files: dict,
                matched: list[dict]) -> None:
    """The saved files are the frames the math used."""
    print()
    print("== The saved frames are the ones the math used")
    if not files["dark"] or not files["measurement"]:
        rep.line(NOT_OK, "no dark or no measurement frames to check")
        return
    scale = FolderMockCamera._pixel_layout(tifffile.imread(files["dark"][0]))[1]
    dark = _mean_of(files["dark"]) / scale
    d = float(np.abs(dark - cal["mean_dark"]).max())
    rep.row(OK if d <= CAL_TOLERANCE_DU else NOT_OK, "dark mean", f"{d:.2e} DU",
            "rebuilt from the files vs Calibration.h5 mean_dark")
    if files["bright"]:
        bright = _mean_of(files["bright"]) / scale - cal["mean_dark"]
        d = float(np.abs(bright - cal["spIm"]).max())
        rep.row(OK if d <= CAL_TOLERANCE_DU else NOT_OK, "bright mean", f"{d:.2e} DU",
                "rebuilt from the files vs Calibration.h5 spIm")

    p = res["params"]
    proc = SCOSProcessor(window_size=int(p["windowSize"]), gain_db=float(p["gain"]),
                         bit_depth=int(p.get("bitDepth", 12)),
                         camera_sn=str(res["meta"].get("camera_sn")))
    proc.scale = scale
    proc.dark_mean = cal["mean_dark"].astype(np.float64)
    proc.dark_var = cal["var_dark"].astype(np.float64)
    proc.bright_var = cal["spVar"].astype(np.float64)
    proc.set_roi(shrink_mask_for_window(cal["mask"], int(p["windowSize"])))
    g, g_rec = proc.resolve_gain(), res["meta"].get("gain_du_per_e")
    if g_rec is not None and abs(g - float(g_rec)) > 1e-12:
        rep.row(CHECK, "G", f"table {g:.6g}, session {float(g_rec):.6g}",
                "the gain table changed since: corrected κ² will differ")
    raw_d, corr_d = 0.0, 0.0
    for m in matched:
        raw, corr, _ = proc.process(tifffile.imread(folder / "Frames" / m["file"]), None)
        i = m["i"]
        raw_d = max(raw_d, abs(raw - res["k2_raw"][i]) / abs(res["k2_raw"][i]))
        corr_d = max(corr_d, abs(corr - res["k2_corr"][i]) / abs(res["k2_corr"][i]))
    worst = max(raw_d, corr_d)
    rep.row(OK if matched and worst <= RERUN_TOLERANCE else NOT_OK, "k2 recomputed",
            f"{len(matched)} frames; raw {raw_d:.1e}, corrected {corr_d:.1e}",
            "largest relative difference from the results file")


def _read(folder: Path) -> tuple[dict, dict]:
    with h5py.File(folder / RESULTS_FILENAME, "r") as f:
        res = {"meta": dict(f["metadata"].attrs), "params": dict(f["Params"].attrs),
               "t": f["timeVec"][:].ravel(), "k2_raw": f["k2_raw"][:].ravel(),
               "k2_corr": f["k2_corr"][:].ravel()}
    with h5py.File(folder / CALIBRATION_FILENAME, "r") as f:
        cal = {"mean_dark": f["dark/mean_dark"][:], "var_dark": f["dark/var_dark"][:],
               "mask": f["dark/mask"][:].astype(bool), "spIm": f["bright/spIm"][:],
               "spVar": f["bright/spVar"][:],
               "dark_n": int(f["dark"].attrs["n_frames"]),
               "bright_n": int(f["bright"].attrs["n_frames"])}
    return res, cal


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("folder", type=Path, help="the session folder")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:          # not a real console stream (tests)
        pass
    folder = args.folder
    if not ((folder / RESULTS_FILENAME).exists() and (folder / CALIBRATION_FILENAME).exists()
            and (folder / "Frames").is_dir()):
        print(f"Not a session folder with saved frames: {folder}")
        return 2

    rep = Report()
    res, cal = _read(folder)
    files = layout(rep, folder, res, cal)
    if files is not None:
        matched = frame_times(rep, folder, res, files)
        same_frames(rep, folder, res, cal, files, matched)

    c = rep.counts
    print()
    print(f"Summary: {c[OK]} OK, {c[CHECK]} CHECK, {c[NOT_OK]} NOT OK"
          + ("  - copy this output and tell Claude" if c[NOT_OK] else ""))
    return 1 if c[NOT_OK] else 0


if __name__ == "__main__":
    sys.exit(main())
