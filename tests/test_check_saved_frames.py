"""
tools/check_saved_frames.py — checks a Save Frames session before it goes to
Vika. The session is written with the app's own writers (FrameWriter,
HDF5Recorder, write_calibration) and its κ² computed with SCOSProcessor, so a
good session must pass every check; each test then breaks one thing the way it
could break for real and expects that check to say NOT OK.
"""

import os
import sys
import time

import numpy as np
import tifffile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.frame_writer import FRAMES_DIRNAME, FrameWriter, frames_folder_name
from core.recorder import (CALIBRATION_FILENAME, RESULTS_FILENAME, HDF5Recorder,
                           write_calibration)
from processor import SCOSProcessor, shrink_mask_for_window
from tools import check_saved_frames

SN, MODEL = "40075248", "a2A1920-160umBAS"      # in the gain table at Mono12, 8 dB
SHAPE, WINDOW, N_CAL, N_MEAS = (40, 44), 7, 12, 10
PARAMS = {"exposureTime": 8.0, "gain": 8.0, "frameRate": 20.0, "windowSize": WINDOW,
          "ROI": np.array([22.0, 20.0, 15.0]), "bitDepth": 12}
NAME = frames_folder_name(8.0, 8.0, 20.0, 100.0)


def _circle():
    yy, xx = np.mgrid[:SHAPE[0], :SHAPE[1]]
    return (xx - 22) ** 2 + (yy - 20) ** 2 <= 15 ** 2


def _session(folder, *, black_level=100.0, name=NAME, not_saved=0):
    """A finished Save Frames session: N_CAL dark + N_CAL bright frames, N_MEAS
    measurement frames of which the last one has no result (still queued at
    Stop SCOS)."""
    rng = np.random.default_rng(7)
    frame = lambda level: rng.integers(level - 60, level + 60, SHAPE).astype(np.uint16)
    writer = FrameWriter(folder / FRAMES_DIRNAME, name, MODEL, SN)
    t0 = time.monotonic()
    dark = [frame(100) for _ in range(N_CAL)]
    bright = [frame(600) for _ in range(N_CAL)]
    meas = [frame(600) for _ in range(N_MEAS)]
    for i, a in enumerate(dark):
        writer.put("dark", a, t0 + 0.01 * i)
    for i, a in enumerate(bright):
        writer.put("bright", a, t0 + 1 + 0.05 * i)
    for i, a in enumerate(meas):
        writer.put("measurement", a, t0 + 2 + 0.05 * i, 0.05 * i)
    writer.close()

    mask = _circle()
    mean_dark = np.mean(dark, axis=0)
    var_dark = np.full(SHAPE, 2.0)
    sp_im = np.mean(bright, axis=0) - mean_dark
    sp_var = np.full(SHAPE, 0.5)
    write_calibration(folder / CALIBRATION_FILENAME, "dark",
                      {"mean_dark": mean_dark.astype(np.float32),
                       "var_dark": var_dark.astype(np.float32), "mask": mask},
                      {"n_frames": N_CAL, "window_size": WINDOW})
    write_calibration(folder / CALIBRATION_FILENAME, "bright",
                      {"spIm": sp_im.astype(np.float32), "spVar": sp_var.astype(np.float32)},
                      {"n_frames": N_CAL, "window_size": WINDOW})

    proc = SCOSProcessor(window_size=WINDOW, gain_db=8.0, bit_depth=12, camera_sn=SN)
    proc.dark_mean = mean_dark.astype(np.float32).astype(np.float64)
    proc.dark_var = var_dark.astype(np.float32).astype(np.float64)
    proc.bright_var = sp_var.astype(np.float32).astype(np.float64)
    proc.set_roi(shrink_mask_for_window(mask, WINDOW))
    meta = {"camera_sn": SN, "camera_black_level": black_level,
            "gain_du_per_e": proc.resolve_gain(), "frames_dropped_queue": 0}
    rec = HDF5Recorder(folder / RESULTS_FILENAME, meta, dict(PARAMS))
    for i, a in enumerate(meas[:-1]):
        raw, corr, mean_i = proc.process(a, None)
        rec.append(0.05 * i, raw, corr, mean_i)
    rec.set_metadata(frames_folder=f"{FRAMES_DIRNAME}/{name}",
                     frames_saved_dark=N_CAL, frames_saved_bright=N_CAL,
                     frames_saved_measurement=N_MEAS, frames_not_saved_dark=0,
                     frames_not_saved_bright=0, frames_not_saved_measurement=not_saved)
    rec.close()


def _files(folder, suffix=""):
    return sorted((folder / FRAMES_DIRNAME / f"{NAME}{suffix}").glob("*.tiff"))


def _run(folder, capsys):
    code = check_saved_frames.main([str(folder)])
    return code, capsys.readouterr().out


def _verdict(out, label):
    """The verdict printed on the row whose label starts with `label`."""
    for line in out.splitlines():
        if line[11:].startswith(label):
            return line[3:9].strip()
    return None


def test_good_session_passes_every_check(tmp_path, capsys):
    _session(tmp_path)
    code, out = _run(tmp_path, capsys)
    assert code == 0, out
    assert "[NOT OK]" not in out and "[CHECK " not in out
    for label in ("black level in name", "dark files", "measurement files",
                  "dark = calibration", "measurement format", "measurement file times",
                  "frame_times rows", "timeVec with a frame", "frames without result",
                  "dark mean", "bright mean", "k2 recomputed"):
        assert _verdict(out, label) == "OK", label
    assert f"{N_MEAS - 1} frames; raw 0.0e+00, corrected 0.0e+00" in out


def test_missing_measurement_file(tmp_path, capsys):
    _session(tmp_path)
    _files(tmp_path)[3].unlink()
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "measurement files") == "NOT OK"
    assert _verdict(out, "frame_times rows") == "NOT OK"
    assert _verdict(out, "timeVec with a frame") == "NOT OK"


def test_changed_measurement_frame(tmp_path, capsys):
    _session(tmp_path)
    path = _files(tmp_path)[2]
    a = tifffile.imread(path)
    a[20, 22] += 50
    stamp = path.stat().st_mtime
    tifffile.imwrite(path, a, photometric="minisblack")
    os.utime(path, (stamp, stamp))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "k2 recomputed") == "NOT OK"
    assert _verdict(out, "measurement file times") == "OK"


def test_changed_dark_frame(tmp_path, capsys):
    _session(tmp_path)
    path = _files(tmp_path, "_dark")[0]
    stamp = path.stat().st_mtime
    tifffile.imwrite(path, tifffile.imread(path) + 30, photometric="minisblack")
    os.utime(path, (stamp, stamp))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "dark mean") == "NOT OK"


def test_wrong_black_level_in_name(tmp_path, capsys):
    """Her FitMeanIm subtracts the name's BL: BL0 instead of BL100 gave κ²_raw ≈ 2800."""
    _session(tmp_path, black_level=0.0)
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "black level in name") == "NOT OK"


def test_file_times_out_of_order(tmp_path, capsys):
    """Her script sorts the frames by file time."""
    _session(tmp_path)
    a, b = _files(tmp_path)[1:3]
    ta, tb = a.stat().st_mtime, b.stat().st_mtime
    os.utime(a, (tb, tb))
    os.utime(b, (ta, ta))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "measurement file times") == "NOT OK"


def test_skipped_frames_reported(tmp_path, capsys):
    _session(tmp_path, not_saved=3)
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "measurement not saved") == "NOT OK"


def test_compressed_file(tmp_path, capsys):
    _session(tmp_path)
    path = _files(tmp_path)[0]
    stamp = path.stat().st_mtime
    tifffile.imwrite(path, tifffile.imread(path), photometric="minisblack",
                     compression="zlib")
    os.utime(path, (stamp, stamp))
    code, out = _run(tmp_path, capsys)
    assert code == 1
    assert _verdict(out, "measurement format") == "NOT OK"


def test_not_a_frames_session(tmp_path, capsys):
    code, out = _run(tmp_path, capsys)
    assert code == 2
    assert "Not a session folder with saved frames" in out
