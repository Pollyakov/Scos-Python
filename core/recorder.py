"""HDF5-based session recorder for SCOS measurements.

Dataset names follow the supervisor's spec (`docs/session_tab`, answers of
2026-09-23) so the file loads in MATLAB without translation: `startTime`,
`timeVec`, `rBFi`, `Intensity` and a `Params` group. The κ² values the result
is derived from (`k2_raw`, `k2_corr`) and the un-normalized `bfi` are kept
alongside them — her list is a floor, not a ceiling, and raw BFi on disk is
what makes a crashed session still worth something.

Appends timeVec, k2_raw, k2_corr, bfi, Intensity to a single .h5 file.
Buffered writes: data accumulates in memory and is flushed to disk every
FLUSH_EVERY appends (or on close), so a crash loses at most FLUSH_EVERY points.

Every submitted frame gets exactly one row — including frames where κ² ≤ 0
made bfi undefined, which are kept with bfi=NaN (see n_invalid) rather than
dropped. `time` staying gap-free matters: downstream FFT-based analysis
(e.g. pulse rate) assumes evenly spaced samples, and a silently missing row
shifts every later timestamp.
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path
from typing import Any

import h5py


FLUSH_EVERY = 300   # flush every N appended results (~15 s at 20 Hz)

# One calibration file per session, holding both kinds (supervisor, 2026-09-23).
# This replaces the two .mat files and the `calibration` group that used to be
# embedded in the results file. `docs/session_tab` asks for separate
# DarkCalibration.h5 / BrightCalibration.h5 — that part of it is superseded.
CALIBRATION_FILENAME = "Calibration.h5"


def write_calibration(path: Path, group: str,
                      datasets: dict[str, Any],
                      attrs: dict[str, Any] | None = None) -> None:
    """Write one calibration group ("dark" or "bright") into the session file.

    Opened in append mode and called twice per session, because dark and bright
    calibration finish minutes apart. Re-writing a group replaces it, so a
    repeated calibration cannot leave half of the previous one behind.

    Arrays are stored float32 with gzip: these are full-frame images
    (1216×1936 for the a2A1920), and three of them uncompressed is ~28 MB.
    """
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "a") as f:
        if group in f:
            del f[group]
        g = f.create_group(group)
        for name, arr in datasets.items():
            if arr is None:
                continue
            g.create_dataset(name, data=np.asarray(arr, dtype=np.float32),
                             compression="gzip", compression_opts=4)
        for k, v in (attrs or {}).items():
            g.attrs[k] = v


def _git_commit() -> str:
    """Short commit hash of the code producing this file, or "unknown".

    Suffixed "-dirty" when the working tree has uncommitted changes: a hash
    that does not describe the code that actually ran is worse than no hash,
    and during development the tree is dirty most of the time.
    """
    def _run(*args: str) -> str:
        return subprocess.run(
            args, cwd=Path(__file__).parent, capture_output=True,
            text=True, timeout=5, check=True,
        ).stdout.strip()

    try:
        commit = _run("git", "rev-parse", "--short", "HEAD")
    except Exception:
        return "unknown"
    try:
        if _run("git", "status", "--porcelain"):
            commit += "-dirty"
    except Exception:
        pass
    return commit


class HDF5Recorder:
    """Appends SCOS results to a persistent HDF5 file.

    Usage::
        rec = HDF5Recorder(path, metadata)
        rec.append(t, k2_raw, k2_corr, mean_i)   # called per frame
        rec.close()                                # on stop or quit
    """

    def __init__(self, path: Path, metadata: dict[str, Any],
                 params_fields: dict[str, Any] | None = None) -> None:
        self._path = Path(path)
        self._buf_t:      list[float] = []
        self._buf_k2raw:  list[float] = []
        self._buf_k2corr: list[float] = []
        self._buf_bfi:    list[float] = []
        self._buf_meani:  list[float] = []
        self._n_flushed = 0
        self._n_invalid  = 0   # rows kept with bfi=NaN because κ² ≤ 0

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._f = h5py.File(self._path, "w")

        # Engineering provenance lives here, separate from Params: Params holds
        # exactly the ten fields the supervisor listed, so nothing unexpected
        # turns up in the struct she reads.
        meta = self._f.create_group("metadata")
        for k, v in metadata.items():
            meta.attrs[k] = v

        # Fixed-length ASCII, not h5py's variable-length UTF-8 default —
        # MATLAB's h5read returns the latter as a cell array instead of a char
        # row, and `datetime(startTime)` then fails on her side.
        start = datetime.datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        self._f.create_dataset(
            "startTime", data=start.encode("ascii"),
            dtype=h5py.string_dtype("ascii", len(start)),
        )

        params = self._f.create_group("Params")
        for k, v in (params_fields or {}).items():
            params.attrs[k] = v
        params.attrs["gitCommit"] = _git_commit()

        kw: dict = {"maxshape": (None,), "chunks": (1024,), "dtype": "float64"}
        self._f.create_dataset("timeVec",   shape=(0,), **kw)
        self._f.create_dataset("k2_raw",    shape=(0,), **kw)
        self._f.create_dataset("k2_corr",   shape=(0,), **kw)
        self._f.create_dataset("bfi",       shape=(0,), **kw)   # raw, un-normalized
        self._f.create_dataset("Intensity", shape=(0,), **kw)   # ROI mean, DU

    def append(self, t: float, k2_raw: float, k2_corr: float,
               mean_i: float) -> None:
        """Append one result row.

        κ² ≤ 0 makes 1/κ² undefined, but the frame itself still happened —
        dropping the row here would leave a gap in `time`, breaking the
        even sampling that downstream FFT-based analysis (e.g. pulse rate)
        depends on. Keep the row and store bfi as NaN ("missing") instead.
        """
        if k2_corr > 0:
            bfi = 1.0 / k2_corr
        else:
            bfi = float("nan")
            self._n_invalid += 1
        self._buf_t.append(t)
        self._buf_k2raw.append(k2_raw)
        self._buf_k2corr.append(k2_corr)
        self._buf_bfi.append(bfi)
        self._buf_meani.append(mean_i)
        if len(self._buf_t) >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        if not self._buf_t:
            return
        n = len(self._buf_t)
        for name, buf in (
            ("timeVec",   self._buf_t),
            ("k2_raw",    self._buf_k2raw),
            ("k2_corr",   self._buf_k2corr),
            ("bfi",       self._buf_bfi),
            ("Intensity", self._buf_meani),
        ):
            ds = self._f[name]
            ds.resize(self._n_flushed + n, axis=0)
            ds[self._n_flushed:] = buf
        self._f.flush()
        self._n_flushed += n
        self._buf_t.clear()
        self._buf_k2raw.clear()
        self._buf_k2corr.clear()
        self._buf_bfi.clear()
        self._buf_meani.clear()

    def append_frame(self, frame: "np.ndarray") -> None:
        """Save one raw camera frame. Creates 'frames' dataset on first call."""
        import numpy as np
        if "frames" not in self._f:
            H, W = frame.shape
            self._f.create_dataset(
                "frames",
                shape=(0, H, W), maxshape=(None, H, W),
                chunks=(1, H, W), dtype=frame.dtype,
                compression="gzip", compression_opts=1,
            )
        ds = self._f["frames"]
        n = ds.shape[0]
        ds.resize(n + 1, axis=0)
        ds[n] = frame
        if n % 10 == 9:
            self._f.flush()

    def write_rbfi(self, norm_constant: float, method: str,
                   window_seconds: float) -> None:
        """Compute and store the final rBFi, once, at the end of the session.

        Called from MainWindow._finish_session() while this file is still open.
        rBFi cannot be written as the session runs: the normalization constant
        depends on the recording's final length (5th percentile for <= 120 s,
        mean for longer — task 10), so it is not known until the operator stops.
        Buffering raw BFi on disk and dividing once here is the supervisor's
        choice (answer 5, 2026-09-23); it also means a large dataset is never
        rewritten.

        The accepted consequence: a session that ends by crash or by closing the
        window has `bfi` and the constant in Params, but no `rBFi`. The file is
        still valid HDF5 and the missing dataset is recoverable offline.

        NaNs in `bfi` (κ² <= 0) stay NaN in `rBFi`, keeping timeVec evenly
        spaced — confirmed as what her analysis expects (answer 6).
        """
        import numpy as np

        self.flush()
        if "rBFi" in self._f:
            del self._f["rBFi"]
        if not norm_constant or not np.isfinite(norm_constant):
            raise ValueError(
                f"Refusing to write rBFi with normalization constant "
                f"{norm_constant!r} — every point would be inf or NaN"
            )
        bfi  = self._f["bfi"][:]
        self._f.create_dataset("rBFi", data=bfi / float(norm_constant))

        p = self._f["Params"]
        p.attrs["normalizationConstant"]  = float(norm_constant)
        p.attrs["normalizationMethod"]    = str(method)
        p.attrs["normalizationWindowSec"] = float(window_seconds)
        self._f.flush()

    def close(self) -> None:
        self.flush()
        self._f.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def n_points(self) -> int:
        return self._n_flushed + len(self._buf_t)

    @property
    def n_invalid(self) -> int:
        """Rows with κ² ≤ 0 — kept in the file with bfi=NaN rather than dropped."""
        return self._n_invalid
