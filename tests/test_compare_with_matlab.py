"""
tools/compare_with_matlab.py — matching the app's results to the MATLAB
script's, frame by frame, through the saved files' names.
"""

import csv
import sys
from pathlib import Path

import h5py
import numpy as np
import scipy.io

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import compare_with_matlab as cmp

FOLDER = "expT8ms_Gain8dB_FR20Hz_BL100DU"


def _session(tmp_path, n=5, drop_result=None):
    """A results file, frame_times.csv and a MATLAB output for n frames.
    MATLAB's frameNames come back in a different order than written."""
    t = np.arange(n) * 0.05
    keep = [i for i in range(n) if i != drop_result]
    with h5py.File(tmp_path / cmp.RESULTS_FILENAME, "w") as f:
        f["timeVec"] = t[keep]
        f["k2_raw"] = 0.1 + 0.01 * np.arange(n)[keep]
        f["k2_corr"] = 0.008 + 0.001 * np.arange(n)[keep]
        f["Intensity"] = 25.0 + np.arange(n)[keep]
        f.create_group("Params").attrs["windowSize"] = 7
        f.create_group("metadata").attrs["frames_folder"] = f"Frames/{FOLDER}"
    frames = tmp_path / "Frames"
    frames.mkdir()
    names = [f"Basler_m__1__{i:06d}.tiff" for i in range(n)]
    with open(frames / "frame_times.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["phase", "file", "index", "t_capture_s", "timeVec_s"])
        w.writerow(["dark", f"{FOLDER}_dark/x.tiff", 0, "1.0", ""])
        for i, nm in enumerate(names):
            w.writerow(["measurement", f"{FOLDER}/{nm}", i, f"{10 + t[i]:.6f}", f"{t[i]:.6f}"])
    order = [1, 0] + list(range(2, n))           # MATLAB sorted by file time
    fn = np.empty((n, 1), dtype=[("name", object)])
    for j, i in enumerate(order):
        fn[j, 0] = (names[i],)
    def cells(v):                                # a MATLAB 1x1 cell holding the vector
        c = np.empty((1, 1), dtype=object)
        c[0, 0] = np.asarray(v, float)[order].reshape(-1, 1)
        return c
    scipy.io.savemat(str(tmp_path / "out.mat"), {
        "frameNames": fn,
        "rawSpeckleContrast": cells(0.1 + 0.01 * np.arange(n)),
        "corrSpeckleContrast": cells(0.008 + 0.001 * np.arange(n)),
        "meanVec": cells(25.0 + np.arange(n)),
    })
    return tmp_path


def test_frames_matched_by_name_not_by_position(tmp_path):
    ses = _session(tmp_path)
    app, mlab = cmp.load_app(ses), cmp.load_matlab(ses / "out.mat")
    ia, im = cmp.match(app, mlab, ses)
    np.testing.assert_array_equal(app["k2_corr"][ia], mlab["corr"][im])
    np.testing.assert_array_equal(app["k2_raw"][ia], mlab["raw"][im])
    assert len(ia) == 5


def test_a_frame_without_a_result_is_left_out(tmp_path):
    ses = _session(tmp_path, drop_result=3)
    app, mlab = cmp.load_app(ses), cmp.load_matlab(ses / "out.mat")
    ia, im = cmp.match(app, mlab, ses)
    assert len(ia) == 4
    np.testing.assert_array_equal(app["Intensity"][ia], mlab["mean"][im])


def test_main_prints_the_table(tmp_path, monkeypatch, capsys):
    ses = _session(tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", str(ses), "--matlab-mat", str(ses / "out.mat")])
    assert cmp.main() == 0
    out = capsys.readouterr().out
    assert "matched 5" in out and "0.00 %" in out


def test_missing_matlab_output(tmp_path, monkeypatch, capsys):
    ses = _session(tmp_path)
    monkeypatch.setattr(sys, "argv", ["x", str(ses)])
    assert cmp.main() == 2
    assert "run her script" in capsys.readouterr().out
