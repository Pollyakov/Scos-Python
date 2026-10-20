"""
FrameWriter — saves raw camera frames as one TIFF per frame, on its own thread.

Plan: docs/Plan_Frame_Saving.md. The purpose (Vika, 2026-10-07) is to rerun the
algorithm on the saved frames — above all her MATLAB script,
SCOSvsTime_WithNoiseSubtraction_Ver2.m — so the layout follows what that script
reads, checked in its code on 2026-10-20:

- one folder per phase inside ``Frames/``: the measurement in ``<name>``, the
  dark calibration in ``<name>_dark`` (the script finds the dark folder by that
  suffix) and the bright calibration in ``<name>_bright``;
- ``<name>`` carries the settings the script parses from the folder name:
  ``expT8ms_Gain8dB_FR20Hz_BL100DU`` (``frames_folder_name()``). It compares
  expT, Gain and BL between the two folders and takes the frame rate from FR;
- files end in exactly ``.tiff`` (``dir('*.tiff')`` — ``.tif`` is not found)
  and are named like Pylon Viewer's, the camera serial number between ``__``:
  ``Basler_<model>__<SN>__<index>.tiff`` — Pylon's recording timestamp is left
  out to keep paths under Windows' 260 characters (``frame_times.csv`` and the
  file times carry the times). The app's ``--mock-folder`` playback reads the
  same pattern;
- 16-bit TIFF, the frame exactly as the camera delivered it — no scaling;
- the script sorts the frames by **file modification time**, so every file's
  time is set to its capture time; the index has a fixed width, so the
  alphabetical order (used by its reader for the dark frames) is the capture
  order too.

``frame_times.csv`` next to the folders ties every file to its capture time, so
the measurement files can be matched one to one with ``timeVec``.

Saving must never slow the measurement: ``put()`` only hands the frame to a
bounded queue and returns at once. When the queue is full the frame is not
saved and is counted (``skipped``) — the caller reports it. A failed write is
logged and counted (``failed``), never raised into the caller's thread.
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import tifffile

logger = logging.getLogger(__name__)

FRAMES_DIRNAME = "Frames"
FRAME_TIMES_FILENAME = "frame_times.csv"

PHASES = ("dark", "bright", "measurement")
_PHASE_SUFFIX = {"dark": "_dark", "bright": "_bright", "measurement": ""}

# ≈ 2 s of frames at 20 Hz — 188 MB of full 1216 × 1936 frames. On the rig PC
# one full frame takes 2.7 ms to write and the disk sustains ≈ 200 frames/s
# (measured 2026-10-20, plan section 1), so the queue only absorbs hiccups.
DEFAULT_MAX_QUEUED = 40

# Windows refuses a path longer than this (MAX_PATH 260, less the final NUL)
# unless long paths are switched on for the whole PC — and MATLAB may not
# read such a path even then. Found 2026-10-21: a session in a deep folder
# lost every bright frame to FileNotFoundError.
MAX_PATH_CHARS = 259

# Six digits: 1 000 000 frames = 13.9 h at 20 Hz. Pylon Viewer uses four, which
# stops sorting alphabetically after 10 000 frames (8 min at 20 Hz).
_INDEX_DIGITS = 6

_CSV_HEADER = ("phase", "file", "index", "t_capture_s", "timeVec_s")


def frames_folder_name(
    exposure_ms: float,
    gain_db: float,
    frame_rate_hz: float,
    black_level: float | None,
) -> str:
    """The measurement folder's name, in the form the MATLAB script parses.

    ``black_level`` is the camera's BlackLevel as read back (100 on the rig
    camera; in playback, the one in the recording's folder name). It must be
    the real value: the script subtracts it from the frames before fitting
    the mean image (``FitMeanIm``), and only there does it not cancel — a
    playback session saved with BL0DU instead of BL100DU gave κ²_raw ≈ 2800
    instead of ≈ 0.1 (2026-10-21). ``None`` (no black level known) → 0.
    """
    bl = 0.0 if black_level is None else black_level
    return (f"expT{exposure_ms:g}ms_Gain{gain_db:g}dB_"
            f"FR{frame_rate_hz:g}Hz_BL{bl:g}DU")


def _file_part(text: str) -> str:
    """A model name or serial number made safe for one ``__``-separated part."""
    return str(text).strip().replace(" ", "-").replace("_", "-") or "unknown"


class FrameWriter:
    """Writes frames handed to ``put()`` on a background thread.

    Thread use: ``put()`` may be called from any thread (the camera thread for
    the measurement, the GUI thread for the calibrations). The caller must not
    modify a frame after handing it over — the camera's frames are its own
    copies and only ever read, so no copy is made here.
    """

    def __init__(
        self,
        frames_dir: Path,
        folder_name: str,
        camera_model: str,
        camera_sn: str,
        max_queued: int = DEFAULT_MAX_QUEUED,
    ) -> None:
        self.frames_dir = Path(frames_dir)
        self.folder_name = folder_name
        # The playback source reports its model as "Basler_a2A1920-160umPRO",
        # the real camera as "a2A1920-160umBAS" — one "Basler_" in the name.
        model = _file_part(camera_model).removeprefix("Basler-")
        self._prefix = f"Basler_{model}__{_file_part(camera_sn)}"

        longest = self.phase_dir("bright") / self._file_name(0)
        if len(str(longest.absolute())) > MAX_PATH_CHARS:
            raise OSError(
                f"The frame files' paths would be {len(str(longest.absolute()))} "
                f"characters long; Windows allows {MAX_PATH_CHARS}. Choose an "
                f"output folder nearer the drive root, or a shorter Recording name.")

        # t_capture is on the time.monotonic() scale; this turns it into the
        # wall-clock time stamped on each file (what MATLAB sorts by).
        self._wall_offset = time.time() - time.monotonic()

        self._lock = threading.Lock()
        self._next_index = {p: 0 for p in PHASES}
        self.saved = {p: 0 for p in PHASES}
        self.skipped = {p: 0 for p in PHASES}
        self.failed = {p: 0 for p in PHASES}

        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self._csv_file = open(self.frames_dir / FRAME_TIMES_FILENAME, "w",
                              newline="", encoding="ascii")
        self._csv = csv.writer(self._csv_file)
        self._csv.writerow(_CSV_HEADER)
        self._made_dirs: set[str] = set()

        self._q: queue.Queue = queue.Queue(maxsize=max_queued)
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="FrameWriter",
                                        daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def _file_name(self, index: int) -> str:
        return f"{self._prefix}__{index:0{_INDEX_DIGITS}d}.tiff"

    def phase_dir(self, phase: str) -> Path:
        """The folder a phase's frames go to."""
        return self.frames_dir / (self.folder_name + _PHASE_SUFFIX[phase])

    def put(
        self,
        phase: str,
        frame: np.ndarray,
        t_capture: float,
        time_vec_s: float | None = None,
    ) -> bool:
        """Queue one frame for saving. Never blocks.

        ``time_vec_s`` is the frame's ``timeVec`` value (measurement only).
        Returns False — and counts the frame in ``skipped`` — when the queue is
        full or the writer is closed.
        """
        if phase not in _PHASE_SUFFIX:
            raise ValueError(f"unknown phase {phase!r}")
        with self._lock:
            if self._closed:
                self.skipped[phase] += 1
                return False
            index = self._next_index[phase]
            try:
                self._q.put_nowait((phase, index, frame, t_capture, time_vec_s))
            except queue.Full:
                self.skipped[phase] += 1
                return False
            self._next_index[phase] = index + 1
            return True

    @property
    def queued(self) -> int:
        """Frames waiting to be written."""
        return self._q.qsize()

    def close(self, timeout: float | None = 30.0) -> bool:
        """Write what is queued, then stop the thread and close the CSV.

        Returns False if the thread had not finished within ``timeout``.
        Safe to call more than once.
        """
        with self._lock:
            first_close = not self._closed
            self._closed = True      # from here on put() queues nothing
        if first_close:
            # Outside the lock: the queue may be full, and the writer thread
            # needs the lock to count what it writes while it makes room.
            self._q.put(None)
        self._thread.join(timeout)
        done = not self._thread.is_alive()
        if done and not self._csv_file.closed:
            self._csv_file.close()
        if not done:
            logger.error("FrameWriter: %d frame(s) still being written after %s s",
                         self._q.qsize(), timeout)
        return done

    def totals(self) -> dict[str, dict[str, int]]:
        """Saved / skipped / failed counts per phase."""
        with self._lock:
            return {"saved": dict(self.saved), "skipped": dict(self.skipped),
                    "failed": dict(self.failed)}

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _run(self) -> None:
        rows_since_flush = 0
        while True:
            item = self._q.get()
            if item is None:
                break
            phase, index, frame, t_capture, time_vec_s = item
            name = self._file_name(index)
            try:
                folder = self.phase_dir(phase)
                if phase not in self._made_dirs:
                    folder.mkdir(parents=True, exist_ok=True)
                    self._made_dirs.add(phase)
                path = folder / name
                tifffile.imwrite(path, frame, photometric="minisblack")
                wall = t_capture + self._wall_offset
                os.utime(path, (wall, wall))
            except Exception:
                with self._lock:
                    self.failed[phase] += 1
                    first = self.failed[phase] == 1
                if first:
                    logger.exception("FrameWriter: could not save %s frame %d", phase, index)
                continue
            with self._lock:
                self.saved[phase] += 1
            self._csv.writerow((phase, f"{folder.name}/{name}", index,
                                f"{t_capture:.6f}",
                                "" if time_vec_s is None else f"{time_vec_s:.6f}"))
            rows_since_flush += 1
            if rows_since_flush >= 100 or self._q.empty():
                self._csv_file.flush()
                rows_since_flush = 0
        self._csv_file.flush()
