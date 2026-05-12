"""HDF5-based session recorder for SCOS measurements.

Appends t, k2_raw, k2_corr, bfi, mean_intensity to a single .h5 file.
Buffered writes: data accumulates in memory and is flushed to disk every
FLUSH_EVERY appends (or on close), so a crash loses at most FLUSH_EVERY points.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py


FLUSH_EVERY = 300   # flush every N appended results (~15 s at 20 Hz)


class HDF5Recorder:
    """Appends SCOS results to a persistent HDF5 file.

    Usage::
        rec = HDF5Recorder(path, metadata)
        rec.append(t, k2_raw, k2_corr, mean_i)   # called per frame
        rec.close()                                # on stop or quit
    """

    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self._path = Path(path)
        self._buf_t:      list[float] = []
        self._buf_k2raw:  list[float] = []
        self._buf_k2corr: list[float] = []
        self._buf_bfi:    list[float] = []
        self._buf_meani:  list[float] = []
        self._n_flushed = 0

        self._f = h5py.File(self._path, "w")

        meta = self._f.create_group("metadata")
        for k, v in metadata.items():
            meta.attrs[k] = v

        kw: dict = {"maxshape": (None,), "chunks": (1024,), "dtype": "float64"}
        self._f.create_dataset("time",           shape=(0,), **kw)
        self._f.create_dataset("k2_raw",         shape=(0,), **kw)
        self._f.create_dataset("k2_corr",        shape=(0,), **kw)
        self._f.create_dataset("bfi",            shape=(0,), **kw)
        self._f.create_dataset("mean_intensity", shape=(0,), **kw)

    def append(self, t: float, k2_raw: float, k2_corr: float,
               mean_i: float) -> None:
        if k2_corr <= 0:
            return
        self._buf_t.append(t)
        self._buf_k2raw.append(k2_raw)
        self._buf_k2corr.append(k2_corr)
        self._buf_bfi.append(1.0 / k2_corr)
        self._buf_meani.append(mean_i)
        if len(self._buf_t) >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        if not self._buf_t:
            return
        n = len(self._buf_t)
        for name, buf in (
            ("time",           self._buf_t),
            ("k2_raw",         self._buf_k2raw),
            ("k2_corr",        self._buf_k2corr),
            ("bfi",            self._buf_bfi),
            ("mean_intensity", self._buf_meani),
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

    def close(self) -> None:
        self.flush()
        self._f.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def n_points(self) -> int:
        return self._n_flushed + len(self._buf_t)
