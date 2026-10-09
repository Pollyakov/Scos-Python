"""
Headless rehearsal of a whole SCOS session on a recorded folder (rig prep 3a).

Drives the real MainWindow the way an operator would — Start Video, wait for
the folder calibration to load, Start SCOS, dark calibration, bright
calibration, normalization, measurement, Stop SCOS — then checks what the
session left on disk. Every check that fails is listed at the end, and the exit
code is non-zero, so one run tells you everything that needs fixing.

Usage:
    venv\\Scripts\\python.exe tools/rehearsal.py                     # 600 + 600 cal frames
    venv\\Scripts\\python.exe tools/rehearsal.py --cal-frames 60     # quick run
    venv\\Scripts\\python.exe tools/rehearsal.py --scenario normal --measure-seconds 30
    venv\\Scripts\\python.exe tools/rehearsal.py --scenario slowdown --cal-frames 60
    venv\\Scripts\\python.exe tools/rehearsal.py --scenario recovery --cal-frames 60

What it can NOT test: the real modal dialogs. Every QMessageBox / QFileDialog
call, and every hand-built QDialog's exec() (the laser-safety windows,
gui/safety_dialog.py), is replaced by a stub that records it and answers the way an operator
following the protocol would, because a real dialog would block this script
forever. A real dialog runs a nested event loop that keeps delivering frames;
the stubs return at once. That path is covered only by the hands-on pass
(docs/simulation_checklist.md, todo step 3f).

Two safety nets keep it from hanging the way the 2026-10-04 run did on an
unstubbed "Measurement Ended" prompt:
  * any dialog title the script does not know is answered Cancel/No and
    reported as a failure — never left open;
  * a watchdog (faulthandler) dumps every thread's stack and kills the process
    after --timeout seconds, whatever it is stuck on.

Scenarios (--scenario) are sets of hooks around the same session; see
SCENARIOS at the bottom: `normal` (3a), `slowdown` (3b, see Slowdown) and
`recovery` (3c, see Recovery). Every scenario ends with the same file checks,
verify_outputs (3e). (3d, the compressed long run, is postponed.)
"""

import argparse
import datetime
import faulthandler
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

DEFAULT_RECORDING = Path(
    r"C:\Users\USER\Scos_Frames_and_Results\expT5ms_Gain24dB_BL100DU_FR40Hz_005")

# The ten fields the supervisor listed for Params (2026-09-23) — no more, no
# fewer. Seven are written when the recorder opens, three by write_rbfi() at
# close; see MainWindow._start_recorder and HDF5Recorder.write_rbfi.
PARAMS_FIELDS = {
    "frameRate", "exposureTime", "gain", "windowSize", "ROI", "bitDepth",
    "gitCommit",
    "normalizationConstant", "normalizationMethod", "normalizationWindowSec",
}
RESULTS_DATASETS = {"startTime", "timeVec", "rBFi", "Intensity",
                    "k2_raw", "k2_corr", "bfi"}
RESULTS_GROUPS   = {"Params", "metadata"}
# Engineering provenance: four written when the recorder opens
# (MainWindow._start_recorder), three at close (_write_frame_accounting).
METADATA_FIELDS  = {"camera_sn", "camera_model", "gain_du_per_e", "gain_source",
                    "time_source", "frames_lost_camera", "frames_dropped_queue"}
SESSION_FILES    = {"Calibration.h5", "rBfi_results.h5", "rBfi_fig.png"}
# How long preview keeps playing after Stop SCOS before Stop Video, so the K1
# check sees displayed frames arrive after the closing message.
CLOSING_HOLD_S   = 3.0

# Dialog titles, as MainWindow spells them.
T_GAIN_WARNING = "Estimated G[DU/e]"
T_FOLDER       = "Choose folder to save this session's results"
T_DARK_PROMPT  = "Calibration — Step 1 of 2: Dark Frames"
T_BRIGHT_PROMPT = "Calibration — Step 2 of 2: Bright Frames"
T_MEAS_ENDED   = "Measurement Ended"
T_LASER_ON     = "Laser May Still Be On"            # red window, U2
T_PROBE_OK     = "Laser Is Off — Remove the Probe"  # after the save, U2
T_PROBE_SKIP   = "Laser-Off Check Could Not Run"
T_PROBE_FAILED = "Do Not Remove the Probe Yet"

# The protocol's order (docs/SCOS_protocol.md:11-17; CLAUDE.md "opening
# dialogs"). The G warning is optional — it appears only when the session's
# gain is not itself a row of the table, as 24 dB is not for this recording.
EXPECTED_DIALOGS = [T_FOLDER, T_DARK_PROMPT, T_BRIGHT_PROMPT, T_MEAS_ENDED,
                    T_PROBE_OK]


# ---------------------------------------------------------------------------
# Results of a run
# ---------------------------------------------------------------------------

@dataclass
class Run:
    """Everything the rehearsal observed. Checks append to `failures`."""
    dialogs:    list[tuple[str, str]]  = field(default_factory=list)  # (kind, title)
    unknown:    list[tuple[str, str]]  = field(default_factory=list)
    exceptions: list[str]              = field(default_factory=list)
    frames:     Counter                = field(default_factory=Counter)
    laser_off_result: object           = "never ran"
    closing_message:  str              = ""   # status bar right after Stop SCOS
    closing_later:    str              = ""   # ... and CLOSING_HOLD_S later
    display_frames_after_stop: int     = 0
    failures:   list[str]              = field(default_factory=list)

    def check(self, ok: bool, what: str) -> bool:
        print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
        if not ok:
            self.failures.append(what)
        return ok


# ---------------------------------------------------------------------------
# Dialog stubs
# ---------------------------------------------------------------------------

def install_dialog_stubs(run: Run, output_root: Path) -> None:
    """Replace every modal entry point MainWindow uses with a recording stub.

    `question()` is answered by title, because the two kinds of question want
    different buttons: the calibration prompts proceed only on Ok, the
    laser-off retry and the short-recording prompt only on Yes. One blanket
    answer is how an earlier version of this script could have looped forever
    on "Laser May Still Be On" (Ok there means "No, check again").
    """
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    B = QMessageBox.StandardButton

    answers = {
        ("question",    T_DARK_PROMPT):   B.Ok,
        ("question",    T_BRIGHT_PROMPT): B.Ok,
        ("information", T_MEAS_ENDED):    B.Ok,
        ("warning",     T_GAIN_WARNING):  B.Ok,
        # Hand-built windows (QDialog.exec): True = accept. On the red window
        # accept is "Continue anyway": the session is saved, and the failed
        # check is still reported below through laser_off_result. Reject there
        # would mean "Check again" — the loop an earlier version of this
        # script could have got stuck in.
        ("dialog",      T_LASER_ON):      True,
        ("dialog",      T_PROBE_OK):      True,
        ("dialog",      T_PROBE_SKIP):    True,
        ("dialog",      T_PROBE_FAILED):  True,
    }
    refuse = {"question": B.Cancel}   # unknown question → back out, never proceed

    def _stub(kind):
        def _f(parent=None, title="", text="", *a, **k):
            run.dialogs.append((kind, title))
            if kind == "critical":
                print(f"  !! critical dialog: {title}: {text}")
            if (kind, title) in answers:
                return answers[(kind, title)]
            if kind not in ("critical", "warning"):
                run.unknown.append((kind, title))
            return refuse.get(kind, B.Ok)
        return staticmethod(_f)

    for kind in ("information", "warning", "critical", "question", "about"):
        setattr(QMessageBox, kind, _stub(kind))

    from PyQt6.QtWidgets import QDialog

    def _exec(dialog):
        title = dialog.windowTitle()
        run.dialogs.append(("dialog", title))
        if answers.get(("dialog", title)) is True:
            dialog.accept()
        else:
            if ("dialog", title) not in answers:
                run.unknown.append(("dialog", title))
            dialog.reject()             # unknown window → back out
        return dialog.result()

    QDialog.exec = _exec

    def _dir(parent=None, caption="", *a, **k):
        run.dialogs.append(("getExistingDirectory", caption))
        return str(output_root)

    def _file(kind):
        def _f(parent=None, caption="", *a, **k):
            run.dialogs.append((kind, caption))
            run.unknown.append((kind, caption))
            return "", ""
        return staticmethod(_f)

    QFileDialog.getExistingDirectory = staticmethod(_dir)
    QFileDialog.getOpenFileName = _file("getOpenFileName")
    QFileDialog.getSaveFileName = _file("getSaveFileName")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _noop(*_a, **_k) -> None:
    return None


@dataclass
class Scenario:
    """Hooks around the one session sequence every scenario shares.

    before_start(w, cam, args) — after auto-load, before Start SCOS. The
        pipeline is rebuilt from `w.processor` at Start SCOS, so patching the
        processor instance here takes effect for the run.
    during_measure(w, cam, args, pump) — replaces the plain "wait until
        --measure-seconds of results" step; must return when it is time to
        press Stop SCOS.
    extra_checks(w, run, folder, args) — scenario-specific assertions, after
        the common ones.
    """
    description:    str
    before_start:   Callable = _noop
    during_measure: Callable | None = None
    extra_checks:   Callable = _noop


def _private_bytes() -> int | None:
    """This process's committed private memory, in bytes (Windows; else None).

    Private bytes rather than the working set: it counts every live NumPy
    buffer whether or not Windows has paged it out, so a frame backlog shows
    up in it however memory-starved the machine is.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(n, ctypes.c_size_t) for n in (
                        "PeakWorkingSetSize", "WorkingSetSize",
                        "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]]

    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    get_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    c = _Counters()
    c.cb = ctypes.sizeof(c)
    return int(c.PrivateUsage) if get_info(get_process(), ctypes.byref(c), c.cb) else None


def _log_text() -> str:
    """Everything logged so far to the run's rehearsal.log."""
    for h in logging.getLogger().handlers:
        if isinstance(h, logging.FileHandler):
            h.flush()
            return Path(h.baseFilename).read_text(encoding="utf-8")
    return ""


class Slowdown:
    """Rig prep 3b — a pipeline that cannot keep up, then one that stops dead.

    Phases, all inside MEASURING (calibration and normalization run at full
    speed, so the session itself stays valid):

      full   FULL_S at full speed — the baseline must not be overloaded, or
             "once per episode" below would be counting an older episode.
      slow   every process() call takes SLOW_DELAY_S longer: the pipeline
             manages ≈ N_WORKERS / (proc + delay) frames/s against PLAYBACK_HZ.
             The input queue fills, overload_detected fires once, and the
             blocking intake throttles the camera — nothing may be dropped yet,
             because a queue slot still frees well inside the 1.5 s put cap.
      stall  one process() call sleeps STALL_S. Results are collected in order,
             so that one frame holds every slot; the camera's put() times out
             every 1.5 s and each timeout evicts — and must count — a frame.
      after  slow again for AFTER_S, so results flow and the Dropped label
             refreshes (it is rewritten once a second, from a result) before
             Stop SCOS, which is pressed while still overloaded.

    Playback runs at PLAYBACK_HZ, not the recording's 40 Hz, because on the
    dev PC the 2.4 Mpx pipeline tops out near 21 frames/s: at 40 Hz the
    "normal" run is itself overloaded from its first seconds and there is no
    clean baseline to slow down from. Set on the camera directly, not through
    the FPS box, so Params.frameRate still reports the recording's rate.

    What it can NOT show — the real camera under overload (todo D5): the mock
    has no frame buffer, so a blocked put() simply delays the next "capture".
    A Basler keeps exposing into Pylon's 20 buffers and loses frames once they
    are full. Since 2026-10-07 camera.py counts those from BlockID gaps and
    stamps each frame with the camera's own exposure time (core/frame_clock.py),
    so frames that waited in the buffers are not bunched — covered by
    tests/test_camera.py on a fake Pylon camera, and by D5 at the rig. Here
    timeVec is checked against the stamps the frame source made.
    """

    PLAYBACK_HZ  = 10.0
    N_WORKERS    = 3
    SLOW_DELAY_S = 0.6     # 3 workers → ≈ 4 frames/s against 10 offered
    STALL_S      = 8.0
    FULL_S       = 3.0
    SLOW_S       = 6.0     # before the stall; the queue fills in ≈ 3–4 s
    AFTER_S      = 4.0
    PUT_CAP_S    = 1.5     # RealtimePipeline.enable_intake's put_timeout_s
    STATUS_AFTER_S = 3.0   # is the overload warning still on screen this late?

    def __init__(self) -> None:
        import threading
        self.lock    = threading.Lock()
        self.delay_s = 0.0
        self.stall_s = 0.0          # pending stall, taken by the next process()
        self.stall_began: float | None = None
        self.stall_ended: float | None = None
        self.phase   = "init"       # calibration + normalization, until during_measure
        self.stamps:    list[float] = []                  # every t_capture emitted
        self.overloads: list[tuple[float, int, str]] = []  # (when, depth, phase)
        self.arrivals:  list[tuple[float, float]] = []    # (when, t) at the GUI
        self.memory:    list[tuple[float, int, int, str]] = []  # (when, bytes, depth, phase)
        self.dropped_before_stall: int | None = None
        self.dropped_after:  int | None = None
        self.label_after:    str | None = None
        self.status_log: list[tuple[float, str]] = []   # every status-bar change
        self._last_sample = 0.0

    # -- hooks ---------------------------------------------------------------

    def before_start(self, w, cam, args) -> None:
        from PyQt6.QtCore import Qt

        w.spn_workers.setValue(self.N_WORKERS)
        cam.set_frame_rate(self.PLAYBACK_HZ)

        # Runs on the camera thread, beside the pipeline's own intake slot:
        # the stamp each frame carried out of the frame source.
        def _stamp(_frame, t_capture):
            self.stamps.append(t_capture)
        cam.frame_ready.connect(_stamp, Qt.ConnectionType.DirectConnection)

        # Every status-bar change, so the K4 check sees an overwrite whenever it
        # happens. A single read STATUS_AFTER_S after the warning was a race:
        # "Frame #…" is rewritten at most every 2.5 s and only when a display
        # frame arrives, which during the stall is every ≈ 1.5 s.
        w.status.messageChanged.connect(
            lambda msg: self.status_log.append((time.monotonic(), msg)))

        # Start SCOS connects the new pipeline to `w._on_overload` by attribute
        # lookup, so an instance attribute set now is what gets connected.
        original_overload = w._on_overload

        def _overload(depth):
            self.overloads.append((time.monotonic(), depth, self.phase))
            original_overload(depth)
        w._on_overload = _overload

        # The pipeline is rebuilt from this processor at Start SCOS.
        original_process = w.processor.process

        def _process(frame, mask):
            out = original_process(frame, mask)
            with self.lock:
                stall, self.stall_s = self.stall_s, 0.0
            if stall:
                self.stall_began = time.monotonic()
                time.sleep(stall)
                self.stall_ended = time.monotonic()
            elif self.delay_s:
                time.sleep(self.delay_s)
            return out
        w.processor.process = _process

    def _watch_arrivals(self, w) -> None:
        from PyQt6.QtCore import Qt

        def _arrived(t, *_rest):
            self.arrivals.append((time.monotonic(), t))
        w._scos_worker.result_ready.connect(_arrived, Qt.ConnectionType.QueuedConnection)

    def _sample(self, w) -> None:
        """Called on every pump iteration: memory every 0.2 s."""
        now = time.monotonic()
        if now - self._last_sample >= 0.2:
            self._last_sample = now
            mem = _private_bytes()
            if mem is not None:
                self.memory.append((now, mem, w._scos_worker.queue_depth, self.phase))

    def _wait(self, w, pump, seconds, label) -> None:
        end = time.monotonic() + seconds
        pump(lambda: (self._sample(w), time.monotonic() >= end)[1], seconds + 60, label)

    def _wait_for(self, w, pump, ready, timeout_s, label) -> None:
        """Wait until ready() — or timeout_s, without failing: the checks then
        report what did not happen, which says more than a TimeoutError."""
        end = time.monotonic() + timeout_s
        pump(lambda: (self._sample(w), ready() or time.monotonic() > end)[1],
             timeout_s + 60, label)

    def during_measure(self, w, cam, args, pump) -> None:
        self._watch_arrivals(w)

        def wait(seconds, label):
            self._wait(w, pump, seconds, label)

        self.phase = "full"
        wait(self.FULL_S, f"{self.FULL_S:.0f} s at full speed ({cam.frame_rate:g} Hz playback)")

        self.phase = "slow"
        self.delay_s = self.SLOW_DELAY_S
        wait(self.SLOW_S, f"{self.SLOW_S:.0f} s slowed by {self.SLOW_DELAY_S} s/frame")
        self.dropped_before_stall = w._scos_worker.dropped_count

        self._stall(w, pump)

        self.phase = "after"
        wait(self.AFTER_S, f"{self.AFTER_S:.0f} s slow again after the stall")
        self.dropped_after = w._scos_worker.dropped_count
        self.label_after   = w.lbl_dropped.text()
        # Stop SCOS is pressed with the pipeline still slowed, as an operator
        # would press it on a machine that cannot keep up.

    def _status_replaced(self) -> tuple[float | None, tuple[float, str] | None]:
        """When the first overload warning appeared on the status bar, and what
        replaced it within STATUS_AFTER_S — (seconds after, text) — if anything."""
        shown = next((t for t, m in self.status_log if "overload" in m.lower()), None)
        if shown is None:
            return None, None
        for t, m in self.status_log:
            if shown < t <= shown + self.STATUS_AFTER_S and "overload" not in m.lower():
                return shown, (t - shown, m)
        return shown, None

    def _stall(self, w, pump) -> None:
        self.phase = "stall"
        with self.lock:
            self.stall_s = self.STALL_S
        self._wait_for(w, pump, lambda: self.stall_ended is not None,
                       self.STALL_S + 30, f"one {self.STALL_S:.0f} s stall")

    def extra_checks(self, w, run: Run, folder, args) -> None:
        pipe = w._scos_worker
        print("\nChecks — slowdown / backpressure")
        print("       overload warnings (queue depth @ phase): "
              + (", ".join(f"{d}@{ph}" for _, d, ph in self.overloads) or "none"))

        before = [o for o in self.overloads if o[2] in ("init", "full")]
        run.check(not before,
                  f"no overload at full speed before the slowdown — a clean baseline "
                  f"({len(before)} fired)")
        during = [o for o in self.overloads if o[2] not in ("init", "full")]
        run.check(len(during) == 1,
                  f"overload_detected fired exactly once for the one episode "
                  f"(fired {len(during)}×)")
        depths = [d for t, _, d, _ in self.memory
                  if self.overloads and t > self.overloads[0][0]]
        if depths:
            print(f"       queue depth after the warning: min {min(depths)}, "
                  f"max {max(depths)} of {pipe.queue_maxsize} "
                  f"(re-arms only at ≤ {int(pipe._OVERLOAD_LOW * pipe.queue_maxsize)})")

        run.check(self.dropped_before_stall == 0,
                  f"slow but not stalled: capture throttled, nothing dropped "
                  f"({self.dropped_before_stall} dropped)")
        stall = (self.stall_ended - self.stall_began
                 if self.stall_began and self.stall_ended else None)
        run.check(stall is not None and stall >= self.STALL_S * 0.9,
                  f"the stall happened ({stall or 0:.1f} s)")
        dropped = pipe.dropped_count
        run.check(dropped > 0,
                  f"the stall dropped frames, and counted them ({dropped}; "
                  f"each one is a {self.PUT_CAP_S} s put() timeout)")
        run.check(self.label_after == f"Dropped: {self.dropped_after}",
                  f"Dropped label shows the count ({self.label_after!r}, "
                  f"count {self.dropped_after})")
        run.check("frame(s) dropped from the input queue" in _log_text(),
                  "the drops are logged in app.log")
        shown, replaced = self._status_replaced()
        run.check(shown is not None and replaced is None,
                  f"overload warning still on the status bar "
                  f"{self.STATUS_AFTER_S:.0f} s after it fired "
                  + ("(never shown)" if shown is None else
                     "" if replaced is None else
                     f"(replaced after {replaced[0]:.1f} s by {replaced[1]!r})"))
        run.check(pipe.error_count == 0, f"no processing errors ({pipe.error_count})")
        self._check_timevec(w, run, folder)
        self._check_memory(w, run)

    def _check_timevec(self, w, run: Run, folder) -> None:
        """timeVec against the stamps the frame source made, and the gaps
        against the drop counter — the core of "keeps capture cadence"."""
        import h5py
        import numpy as np

        pipe    = w._scos_worker
        dropped = pipe.dropped_count
        t0 = pipe.t0_capture
        res = folder / "rBfi_results.h5" if folder is not None else None
        if not run.check(t0 is not None and res is not None and res.exists(),
                         "results file and t=0 available"):
            return
        with h5py.File(res, "r") as f:
            t_file = f["timeVec"][:]
        # Same float64 subtraction the pipeline does, so equality is exact.
        stamps = np.asarray(self.stamps, dtype=np.float64) - t0
        is_stamp = np.isin(t_file, stamps)
        run.check(bool(is_stamp.all()),
                  f"every timeVec value is a capture stamp minus t0 "
                  f"({int((~is_stamp).sum())} of {len(t_file)} are not)")
        inside  = stamps[(stamps >= t_file[0]) & (stamps <= t_file[-1])]
        missing = np.setdiff1d(inside, t_file)
        run.check(len(missing) == dropped + pipe.error_count,
                  f"every frame missing from timeVec is a counted drop "
                  f"({len(missing)} missing, {dropped} dropped + "
                  f"{pipe.error_count} errors)")
        if len(missing):
            print(f"       missing at t = {', '.join(f'{m:.2f}' for m in missing[:12])}"
                  f"{' …' if len(missing) > 12 else ''} s")
        gaps = np.diff(t_file)
        print(f"       timeVec spacing: median {np.median(gaps) * 1000:.0f} ms, "
              f"max {gaps.max():.2f} s  (playback {1000 / self.PLAYBACK_HZ:.0f} ms)")

        # The guard that keeps the timestamp checks from passing vacuously:
        # results must really have arrived late. Stamped on arrival, they
        # would all be off by this much.
        lags = [arr - (t0 + t) for arr, t in self.arrivals]
        max_lag = max(lags) if lags else 0.0
        run.check(max_lag >= 2.0,
                  f"results reached the GUI up to {max_lag:.1f} s after capture, "
                  f"yet timeVec holds the capture times")

    # Phases that run overloaded, and the one judged "flat" against the
    # slow phase's full-queue level — Recovery overrides both.
    LOADED_PHASES = ("slow", "stall", "after")
    FLAT_PHASE    = "after"

    def _check_memory(self, w, run: Run) -> None:
        """Bounded while the queue fills, flat once it is full.

        Memory legitimately rises by the frames the pipeline holds — queue,
        in flight, one with the dispatcher and one in the camera's put(). On
        top of that sit short spikes of up to ≈ 150 MB (worker temporaries,
        the GUI's float64 copy of a frame for its ⟨I⟩/p5/p95 labels), so
        single samples are too noisy to judge by: the 2026-10-06 clean runs
        peaked anywhere from 165 to 247 MB above full speed. Each phase is
        therefore judged by its median: no phase may sit more than twice the
        held frames above full speed (clean ≈ +80 MB; the unbounded-in-flight
        mutation +410 MB), and once the queue is full (slow phase, after the
        warning) the level must stop climbing. A leak fails both.
        """
        import numpy as np

        if not self.memory:
            print("       memory: not measured (Windows only)")
            return
        pipe     = w._scos_worker
        info     = w.camera.get_info()
        frame_mb = info["width"] * info["height"] * 2 / 2**20   # uint16 frames
        held     = pipe.queue_maxsize + 2 * self.N_WORKERS + 2
        for ph in ("full", *self.LOADED_PHASES):
            mb = [m / 2**20 for _, m, _, p in self.memory if p == ph]
            if mb:
                print(f"       memory {ph:5s} {len(mb):3d} samples: median "
                      f"{np.median(mb):.0f}, min {min(mb):.0f}, max {max(mb):.0f} MB")

        def median_mb(keep) -> float | None:
            mb = [m / 2**20 for t, m, _, p in self.memory if keep(t, p)]
            return float(np.median(mb)) if mb else None

        full_mb  = median_mb(lambda t, p: p == "full")
        loaded   = [m for ph in self.LOADED_PHASES
                    if (m := median_mb(lambda t, p, ph=ph: p == ph)) is not None]
        peak_mb  = max(m for _, m, _, _ in self.memory) / 2**20
        bound_mb = 2 * held * frame_mb
        rise_mb  = (max(loaded) - full_mb) if loaded and full_mb is not None else None
        run.check(rise_mb is not None and rise_mb <= bound_mb,
                  f"memory bounded under overload: highest phase median "
                  f"{'n/a' if rise_mb is None else f'{rise_mb:+.0f} MB'} above full speed "
                  f"(single-sample peak {peak_mb - (full_mb or 0):+.0f}), bound "
                  f"{bound_mb:.0f} MB (2 × {held} held frames × {frame_mb:.1f} MB)")

        # From the warning on, the queue is full. Without a warning, the second
        # half of the slow phase — so a leak that also hides the overload
        # still fails this check on its own numbers.
        slow_t = [t for t, _, _, p in self.memory if p == "slow"]
        t_full = (self.overloads[0][0] if self.overloads
                  else (slow_t[0] + slow_t[-1]) / 2 if slow_t else None)
        full_q = median_mb(lambda t, p: p == "slow" and t_full and t > t_full)
        after_mb = median_mb(lambda t, p: p == self.FLAT_PHASE)
        rise     = (after_mb - full_q) if full_q is not None and after_mb is not None else None
        limit_mb = 10 * frame_mb
        run.check(rise is not None and rise <= limit_mb,
                  f"memory flat once the queue is full: "
                  f"{'n/a' if rise is None else f'{rise:+.0f} MB'} from the slow "
                  f"phase to the {self.FLAT_PHASE} phase, limit {limit_mb:.0f} MB "
                  f"(10 frames)")


class Recovery(Slowdown):
    """Rig prep 3c — an overload that ends, then a second one.

    Slowdown (3b) stops while still overloaded, so it never shows the pipeline
    getting well again. Here, after the same slow + stall episode:

      recover   full speed again. The queue must drain below the 50 % re-arm
                mark, the overload flag must reset, the drop count must stop
                rising and results must catch up with capture.
      slow2     slowed again (no stall): overload_detected must fire a second
                time. That is the end-to-end proof the flag re-armed — without
                it a slowdown an hour into a session would go unwarned.
      recover2  full speed until Stop SCOS, so the session ends healthy.

    Episode 1 keeps the stall because slowing alone drops nothing (3b): without
    drops, "drops stop" would be checking nothing. Warnings are assigned to
    time windows, not to the phase string, because overload_detected reaches
    the GUI through the event queue and the first one can land in "stall".

    The K4 check (warning still on the status bar) is left to `slowdown`, where
    it fails on purpose until 4a; a clean run of this scenario exits 0.
    """

    SETTLE_S        = 4.0    # at full speed after the queue first drains to ≤ 50 %
    DRAIN_TIMEOUT_S = 30.0
    SLOW2_TIMEOUT_S = 20.0   # the queue fills in ≈ 3–4 s at SLOW_DELAY_S
    SLOW2_AFTER_S   = 2.0    # kept slow after the 2nd warning — room for a re-fire
    LAG_OK_S        = 1.0    # capture → GUI once recovered (≈ 0.2 s at full speed)

    LOADED_PHASES = ("slow", "stall", "slow2")
    FLAT_PHASE    = "slow2"

    def __init__(self) -> None:
        super().__init__()
        self.marks:   dict[str, float] = {}   # phase boundaries, monotonic
        self.drops:   dict[str, int]   = {}   # dropped_count at those marks
        self.rearmed: bool | None = None      # pipeline flag after recover
        self.label_end: str | None = None

    def _mark(self, w, name: str) -> None:
        self.marks[name] = time.monotonic()
        self.drops[name] = w._scos_worker.dropped_count

    def during_measure(self, w, cam, args, pump) -> None:
        self._watch_arrivals(w)
        pipe    = w._scos_worker
        low     = int(pipe._OVERLOAD_LOW * pipe.queue_maxsize)
        drained = lambda: pipe.queue_depth <= low

        self.phase = "full"
        self._wait(w, pump, self.FULL_S,
                   f"{self.FULL_S:.0f} s at full speed ({cam.frame_rate:g} Hz playback)")

        # Episode 1 — as in Slowdown.
        self._mark(w, "slow")
        self.phase = "slow"
        self.delay_s = self.SLOW_DELAY_S
        self._wait(w, pump, self.SLOW_S,
                   f"{self.SLOW_S:.0f} s slowed by {self.SLOW_DELAY_S} s/frame")
        self._stall(w, pump)

        # Recovery 1. One put() can still be mid-timeout when the stall ends,
        # so drops are baselined when the queue first drains, not at the switch.
        self._mark(w, "recover")
        self.phase = "recover"
        self.delay_s = 0.0
        self._wait_for(w, pump, drained, self.DRAIN_TIMEOUT_S,
                       f"queue drained to ≤ {low} at full speed")
        self._mark(w, "drained")
        self._wait(w, pump, self.SETTLE_S, f"{self.SETTLE_S:.0f} s recovered")
        self.rearmed = not pipe._overloaded

        # Episode 2 — slow only.
        self._mark(w, "slow2")
        self.phase = "slow2"
        self.delay_s = self.SLOW_DELAY_S
        self._wait_for(w, pump,
                       lambda: any(t >= self.marks["slow2"] for t, *_ in self.overloads),
                       self.SLOW2_TIMEOUT_S, "second overload warning")
        self._wait(w, pump, self.SLOW2_AFTER_S, f"{self.SLOW2_AFTER_S:.0f} s more slowed")

        # Recovery 2, then Stop SCOS at full speed.
        self._mark(w, "recover2")
        self.phase = "recover2"
        self.delay_s = 0.0
        self._wait_for(w, pump, drained, self.DRAIN_TIMEOUT_S,
                       f"queue drained to ≤ {low} again")
        self._mark(w, "drained2")
        self._wait(w, pump, self.SETTLE_S, f"{self.SETTLE_S:.0f} s recovered")
        self._mark(w, "end")
        self.label_end = w.lbl_dropped.text()

    def extra_checks(self, w, run: Run, folder, args) -> None:
        pipe = w._scos_worker
        m, d = self.marks, self.drops
        print("\nChecks — overload recovery")
        print("       overload warnings (queue depth @ phase): "
              + (", ".join(f"{dp}@{ph}" for _, dp, ph in self.overloads) or "none"))
        print("       phase starts (s after the slowdown) / dropped so far: "
              + ", ".join(f"{k} {m[k] - m['slow']:.1f}/{d[k]}" for k in m))
        if not run.check({"slow", "recover", "drained", "slow2", "recover2",
                          "drained2", "end"} <= m.keys(), "every phase ran"):
            return

        def fired(t_from: float, t_to: float = float("inf")) -> int:
            return sum(1 for t, *_ in self.overloads if t_from <= t < t_to)

        low = int(pipe._OVERLOAD_LOW * pipe.queue_maxsize)
        run.check(fired(0, m["slow"]) == 0,
                  f"no overload at full speed before the slowdown — a clean baseline "
                  f"({fired(0, m['slow'])} fired)")
        run.check(fired(m["slow"], m["drained"]) == 1,
                  f"episode 1: overload_detected fired exactly once "
                  f"(fired {fired(m['slow'], m['drained'])}×)")
        run.check(d["drained"] > 0,
                  f"episode 1's stall dropped frames — so 'drops stop' below means "
                  f"something ({d['drained']} dropped)")

        drain_s = m["drained"] - m["recover"]
        run.check(drain_s < self.DRAIN_TIMEOUT_S,
                  f"speed restored → queue below the {low}/{pipe.queue_maxsize} "
                  f"re-arm mark in {drain_s:.1f} s")
        run.check(bool(self.rearmed),
                  f"overload flag reset once the queue drained "
                  f"(pipeline._overloaded = {not self.rearmed})")
        run.check(fired(m["drained"], m["slow2"]) == 0,
                  f"no warning while recovered ({fired(m['drained'], m['slow2'])} fired)")
        run.check(d["slow2"] == d["drained"],
                  f"drops stopped once recovered "
                  f"({d['slow2'] - d['drained']} more in {self.SETTLE_S:.0f} s)")

        n2 = fired(m["slow2"], m["drained2"])
        run.check(n2 == 1,
                  f"episode 2: the re-armed warning fired again, exactly once "
                  f"(fired {n2}×)")
        run.check(fired(m["drained2"]) == 0,
                  f"no warning after the second recovery ({fired(m['drained2'])} fired)")
        run.check(d["end"] == d["drained"],
                  f"no frame dropped from the first recovery to Stop SCOS "
                  f"({d['end'] - d['drained']} dropped; slow without a stall must "
                  f"throttle, not drop)")
        run.check(self.label_end == f"Dropped: {d['end']}",
                  f"Dropped label shows the count ({self.label_end!r}, count {d['end']})")

        # Speed restored: in the last seconds, results arrive at the playback
        # rate and soon after capture — the backlog is really gone.
        t0 = pipe.t0_capture or 0.0
        base = [a - (t0 + t) for a, t in self.arrivals if a < m["slow"]]
        tail = [(a, a - (t0 + t)) for a, t in self.arrivals
                if a >= m["drained2"] + 1.0]
        span = m["end"] - (m["drained2"] + 1.0)
        rate = len(tail) / span if span > 0 else 0.0
        lag  = max((g for _, g in tail), default=float("inf"))
        if base:
            print(f"       capture → GUI at full speed before the slowdown: "
                  f"max {max(base):.2f} s")
        run.check(lag <= self.LAG_OK_S,
                  f"recovered: results reach the GUI ≤ {self.LAG_OK_S:.0f} s after "
                  f"capture (max {lag:.2f} s over the last {span:.1f} s)")
        run.check(rate >= 0.8 * self.PLAYBACK_HZ,
                  f"recovered: {rate:.1f} results/s against {self.PLAYBACK_HZ:g} Hz playback")
        run.check(pipe.error_count == 0, f"no processing errors ({pipe.error_count})")

        self._check_timevec(w, run, folder)
        self._check_memory(w, run)
        # Printed, not checked: the backlog's frames are freed again (2026-10-07,
        # recover2 vs full: +8, +38, +42 MB), but phase medians over 4 s still
        # carry the ≈ 150 MB worker spikes, so any limit tight enough to mean
        # something failed on noise. A growing backlog fails _check_memory.
        import numpy as np
        for ph in ("recover", "recover2"):
            mb = [b / 2**20 for _, b, _, p in self.memory if p == ph]
            if mb:
                print(f"       memory {ph:8s} {len(mb):3d} samples: median "
                      f"{np.median(mb):.0f}, min {min(mb):.0f}, max {max(mb):.0f} MB")


_slowdown = Slowdown()
_recovery = Recovery()

SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("one clean session at full speed"),
    "slowdown": Scenario(
        f"processing slowed, then stalled once, during the measurement "
        f"(playback at {Slowdown.PLAYBACK_HZ:g} Hz)",
        before_start=_slowdown.before_start,
        during_measure=_slowdown.during_measure,
        extra_checks=_slowdown.extra_checks,
    ),
    "recovery": Scenario(
        f"overloaded, then full speed again, then overloaded a second time "
        f"(playback at {Recovery.PLAYBACK_HZ:g} Hz)",
        before_start=_recovery.before_start,
        during_measure=_recovery.during_measure,
        extra_checks=_recovery.extra_checks,
    ),
}


# ---------------------------------------------------------------------------
# The rehearsal
# ---------------------------------------------------------------------------

def _frame_level(folder: Path, scale: float) -> float:
    """Mean of the first TIFF in `folder`, in DU — a reference brightness."""
    import numpy as np
    import tifffile
    first = sorted(list(folder.glob("*.tif")) + list(folder.glob("*.tiff")))[0]
    return float(np.mean(tifffile.imread(str(first)))) / scale


def rehearse(args, run: Run) -> Path | None:
    import numpy as np
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from core.session import State
    from folder_camera import FolderMockCamera
    from gui.main_window import MainWindow

    scenario = SCENARIOS[args.scenario]

    def pump(ready, timeout_s, label):
        t0 = time.monotonic()
        while not ready():
            if time.monotonic() - t0 > timeout_s:
                raise TimeoutError(f"timed out after {timeout_s:.0f} s waiting for: "
                                   f"{label} (state={w._state.name})")
            app.processEvents()
            time.sleep(0.01)
        print(f"  [{time.monotonic() - t0:6.1f} s] {label}")

    cam = FolderMockCamera(args.recording)
    w = MainWindow(camera=cam)

    # What the GUI receives, by state and playback source — diagnostics only.
    # Frames queued before a source switch legitimately arrive in the next
    # state (_flush_stale_frames drops them), so this is never asserted on.
    original_on_frame = w._on_scos_frame

    def _spy(frame, t_capture):
        run.frames[(w._state.name, cam.playback_source)] += 1
        original_on_frame(frame, t_capture)

    cam.frame_ready.disconnect(w._on_scos_frame)
    cam.frame_ready.connect(_spy, Qt.ConnectionType.QueuedConnection)

    # The laser-off check reports its outcome only through the closing status
    # message — so capture its return value directly as well, in case that
    # message is lost again (K1).
    original_laser_check = w._laser_off_check

    def _laser_check(mask):
        run.laser_off_result = original_laser_check(mask)
        return run.laser_off_result

    w._laser_off_check = _laser_check

    folder = None
    try:
        if args.cal_frames is not None:
            w.spn_n1.setValue(args.cal_frames)
            w.spn_n2.setValue(args.cal_frames)
        w.spn_norm_seconds.setValue(args.norm_seconds)
        n_cal = max(w.spn_n1.value(), w.spn_n2.value())
        # Playback is nominally 40 Hz, but the GUI-thread collectors take
        # ~9 frames/s of 2.4 Mpx here (600 dark frames: 69 s, 2026-10-05).
        # Allow down to 2 Hz; the --timeout watchdog still catches a real hang.
        cal_timeout = max(120.0, n_cal / 2.0)
        print(f"calibration frames: dark {w.spn_n1.value()}, bright {w.spn_n2.value()}; "
              f"normalization {w.spn_norm_seconds.value()} s; "
              f"measure {args.measure_seconds} s")

        print("Start Video")
        w.btn_start_video.setChecked(True)
        pump(lambda: w.btn_start_scos.isEnabled(), 300, "folder calibration loaded")

        scenario.before_start(w, cam, args)

        # startTime is written when the recorder opens, a few seconds after
        # this; it must fall between here and the checks.
        wall_start = datetime.datetime.now().replace(microsecond=0)
        print("Start SCOS")
        w.btn_start_scos.setChecked(True)
        pump(lambda: w._state is not State.DARK_CAL and w._state is not State.PREVIEW,
             cal_timeout, "dark calibration finished")
        pump(lambda: w._state in (State.MEASURING_INIT, State.MEASURING),
             cal_timeout, "bright calibration finished")
        pump(lambda: w._state is State.MEASURING,
             args.norm_seconds * 10 + 60, "normalization finished")
        folder = w._session_folder

        dark_du  = float(np.mean(w.processor.dark_mean))
        dark_ref = _frame_level(cam.get_dark_dir(), w.processor.scale)
        main_ref = _frame_level(Path(args.recording), w.processor.scale)
        print(f"  dark_mean {dark_du:.2f} DU  (dark folder ≈ {dark_ref:.2f}, "
              f"recording ≈ {main_ref:.2f})")
        print(f"  provisional norm {w._bfi_norm:.4f} by {w._bfi_norm_method}")

        if scenario.during_measure is not None:
            scenario.during_measure(w, cam, args, pump)
        else:
            pump(lambda: w._last_result_t >= args.measure_seconds,
                 args.measure_seconds * 10 + 60,
                 f"{args.measure_seconds} s of measurement")

        print("Stop SCOS")
        w.btn_start_scos.setChecked(False)
        final_state = w._state
        # K1: the closing message must outlast the frames that keep arriving
        # in PREVIEW — before the fix, the next displayed frame replaced it.
        run.closing_message = w.status.currentMessage()
        frames_at_stop = w._frame_count
        t_stop = time.monotonic()
        pump(lambda: time.monotonic() - t_stop >= CLOSING_HOLD_S,
             CLOSING_HOLD_S + 10, f"{CLOSING_HOLD_S:.0f} s of preview after Stop SCOS")
        run.closing_later = w.status.currentMessage()
        run.display_frames_after_stop = w._frame_count - frames_at_stop
        w.btn_start_video.setChecked(False)
        app.processEvents()

        # ---------------- checks ----------------
        print("\nChecks — session")
        run.check(final_state is State.PREVIEW,
                  f"back in PREVIEW after Stop SCOS (is {final_state.name})")
        run.check(abs(dark_du - dark_ref) < abs(dark_du - main_ref),
                  f"dark calibration came from dark frames "
                  f"({dark_du:.1f} DU is nearer {dark_ref:.1f} than {main_ref:.1f})")
        # spIm = mean bright frame − dark_mean (Calibration.h5). Built from
        # laser-off frames — what _flush_stale_frames exists to prevent — its
        # mean would sit near 0 instead of near recording − dark.
        bright_du = None
        if folder is not None and (folder / "Calibration.h5").exists():
            import h5py
            with h5py.File(folder / "Calibration.h5", "r") as f:
                if "bright/spIm" in f:
                    bright_du = float(np.mean(f["bright/spIm"][:])) + dark_du
        if run.check(bright_du is not None, "Calibration.h5 has bright/spIm"):
            run.check(abs(bright_du - main_ref) < abs(bright_du - dark_ref),
                      f"bright calibration came from laser-on frames "
                      f"({bright_du:.1f} DU is nearer {main_ref:.1f} than {dark_ref:.1f})")
        run.check(run.laser_off_result is None,
                  f"laser-off check passed (returned {run.laser_off_result!r})")
        run.check(run.closing_message.startswith("Session finished")
                  and run.closing_later == run.closing_message
                  and run.display_frames_after_stop > 0,
                  f"closing message still on the status bar {CLOSING_HOLD_S:.0f} s "
                  f"after Stop SCOS, over {run.display_frames_after_stop} displayed "
                  f"frame(s) (K1; now {run.closing_later!r})")
        verify_outputs(run, folder, w, cam, args, wall_start)
        scenario.extra_checks(w, run, folder, args)
    finally:
        w.close()
        app.processEvents()
        # closeEvent gives the pipeline 2 s. A thread still running after
        # that is killed by interpreter shutdown, mid-task — so report it,
        # then let it finish, so the run itself ends cleanly.
        print("\nChecks — shutdown")
        pipe = w._scos_worker
        still_running = pipe.isRunning()
        if still_running:
            t_close = time.monotonic()
            pipe.wait(120_000)
            print(f"       pipeline thread needed {time.monotonic() - t_close:.1f} s "
                  f"more after the window closed")
        run.check(not still_running,
                  "SCOS pipeline thread had stopped when the window closed")
    return folder


def verify_outputs(run: Run, folder: Path | None, w, cam, args,
                   wall_start: datetime.datetime) -> None:
    """What the session must have left on disk — that it is there, and (rig
    prep 3e) that the numbers in it are right: rBFi is bfi divided by the
    constant in Params, that constant is recomputed from the file's own
    baseline rows, and the provenance matches what the GUI showed."""
    import h5py
    import numpy as np
    from core.session import choose_norm_method, normalization_constant

    print("\nChecks — session folder")
    if not run.check(folder is not None and folder.is_dir(),
                     f"session folder exists ({folder})"):
        return
    names = {p.name for p in folder.iterdir()}
    run.check(names == SESSION_FILES,
              f"exactly {sorted(SESSION_FILES)} (found {sorted(names)})")
    fig = folder / "rBfi_fig.png"
    if fig.exists():
        run.check(fig.stat().st_size > 5_000,
                  f"rBfi_fig.png is a real image ({fig.stat().st_size} bytes)")

    def sat_names(f) -> list[str]:
        hits = []

        def visit(name, obj):
            for n in [name, *obj.attrs.keys()]:
                if "sat" in n.lower():
                    hits.append(f"{name}:{n}")
        for k in f.attrs.keys():
            if "sat" in k.lower():
                hits.append(f"/:{k}")
        f.visititems(visit)
        return hits

    cal = folder / "Calibration.h5"
    if cal.exists():
        with h5py.File(cal, "r") as f:
            run.check(set(f.keys()) == {"dark", "bright"},
                      f"Calibration.h5 has groups dark + bright (has {sorted(f.keys())})")
            hits = sat_names(f)
            run.check(not hits, f"no satCapacity in Calibration.h5 {hits or ''}")
            frame_shape = tuple(w.processor.dark_mean.shape)
            for group, names, spin in (("dark", ("mean_dark", "var_dark", "mask"), w.spn_n1),
                                       ("bright", ("spIm", "spVar"), w.spn_n2)):
                if group not in f:
                    continue
                g = f[group]
                for name in names:
                    if not run.check(name in g, f"Calibration.h5 has {group}/{name}"):
                        continue
                    a = g[name][:]
                    run.check(a.shape == frame_shape and bool(np.isfinite(a).all()),
                              f"{group}/{name} is a finite {frame_shape} image "
                              f"(shape {a.shape}, "
                              f"{int((~np.isfinite(a)).sum())} non-finite)")
                n_frames = g.attrs.get("n_frames")
                run.check(n_frames == spin.value(),
                          f"{group} n_frames = {n_frames} (spinbox {spin.value()})")

    res = folder / "rBfi_results.h5"
    if not res.exists():
        return
    with h5py.File(res, "r") as f:
        keys     = set(f.keys())
        expected = RESULTS_DATASETS | RESULTS_GROUPS
        run.check(keys == expected,
                  f"rBfi_results.h5 holds exactly the expected entries "
                  f"(missing {sorted(expected - keys)}, extra {sorted(keys - expected)})")
        hits = sat_names(f)
        run.check(not hits, f"no satCapacity in rBfi_results.h5 {hits or ''}")
        if not RESULTS_DATASETS <= keys or "Params" not in keys:
            return

        t   = f["timeVec"][:]
        k2  = f["k2_corr"][:]
        rb  = f["rBFi"][:]
        n   = len(t)
        lens = {k: len(f[k]) for k in ("timeVec", "rBFi", "Intensity",
                                         "k2_raw", "k2_corr", "bfi")}
        run.check(n > 0 and len(set(lens.values())) == 1,
                  f"all series the same length, n = {n} ({lens})")
        run.check(n > 1 and bool(np.all(np.diff(t) > 0)),
                  "timeVec strictly increasing")
        if n:
            run.check(t[-1] >= args.measure_seconds,
                      f"timeVec reaches {args.measure_seconds} s (ends at {t[-1]:.2f} s)")
            print(f"       κ²_corr mean {k2.mean():.6f}, positive {(k2 > 0).sum()}/{n}; "
                  f"rBFi mean {np.nanmean(rb):.4f}")
        run.check(bool(np.all(k2 > 0)), f"corrected κ² positive everywhere "
                                        f"({int((k2 <= 0).sum())} not)")
        run.check(not np.isnan(rb).any(), f"no NaN in rBFi ({int(np.isnan(rb).sum())})")

        p = dict(f["Params"].attrs)
        run.check(set(p) == PARAMS_FIELDS,
                  f"Params has exactly the ten fields "
                  f"(missing {sorted(PARAMS_FIELDS - set(p))}, "
                  f"extra {sorted(set(p) - PARAMS_FIELDS)})")
        rec = cam._recording_params
        expect = {
            "frameRate":    rec.get("frame_rate"),
            "exposureTime": rec["exposure_us"] / 1000 if "exposure_us" in rec else None,
            "gain":         rec.get("gain_db"),
            "windowSize":   7,
            "bitDepth":     rec.get("bit_depth"),
            "normalizationWindowSec": float(args.norm_seconds),
        }
        for k, v in expect.items():
            if v is not None and k in p:
                run.check(np.isclose(float(p[k]), float(v)),
                          f"Params.{k} = {p[k]} (recording: {v})")
        if "normalizationMethod" in p and n:
            want = choose_norm_method(float(t[-1]))
            run.check(str(p["normalizationMethod"]) == want,
                      f"Params.normalizationMethod = {p['normalizationMethod']} "
                      f"(a {t[-1]:.0f} s recording → {want})")
        if "normalizationConstant" in p:
            c = float(p["normalizationConstant"])
            run.check(np.isfinite(c) and c > 0, f"Params.normalizationConstant = {c:.4f}")

        # ---- 3e: the numbers, not just their presence ----
        bfi = f["bfi"][:]
        valid = k2 > 0
        run.check(bool(np.allclose(bfi[valid], 1.0 / k2[valid], rtol=1e-12))
                  and bool(np.isnan(bfi[~valid]).all()),
                  "bfi = 1/k2_corr where κ² > 0, NaN elsewhere")
        if "normalizationConstant" in p and n:
            c = float(p["normalizationConstant"])
            ok = bool(np.allclose(rb, bfi / c, rtol=1e-12, equal_nan=True))
            run.check(ok, "rBFi = bfi / Params.normalizationConstant "
                          f"(worst ratio {np.nanmax(np.abs(rb * c / bfi - 1)):.2e} off)")
            # The baseline is every valid row up to and including the first
            # valid one at or past norm_seconds: _on_scos_result appends the
            # point to the buffer *before* testing whether the window closed.
            norm_s = float(w._norm_seconds)
            closing = np.flatnonzero(valid & (t >= norm_s))
            if run.check(closing.size > 0,
                         f"a valid row at or past the {norm_s:g} s window"):
                base = bfi[:closing[0] + 1][valid[:closing[0] + 1]]
                method = choose_norm_method(float(t[-1]))
                again = normalization_constant(base, method)
                run.check(np.isclose(again, c, rtol=1e-12),
                          f"constant recomputed from the file's first {base.size} "
                          f"rows by {method} = {again:.6g} (Params: {c:.6g})")

        roi = [float(w._roi_circ.get(k, -1)) for k in ("cx", "cy", "r")]
        run.check("ROI" in p and np.allclose(np.asarray(p["ROI"], float), roi),
                  f"Params.ROI = {[round(float(x), 2) for x in p.get('ROI', [])]} "
                  f"(GUI: {[round(x, 2) for x in roi]})")
        commit = str(p.get("gitCommit", ""))
        run.check(bool(commit) and commit != "unknown",
                  f"Params.gitCommit = {commit!r}")

        raw = f["startTime"][()]
        raw = raw.decode("ascii") if isinstance(raw, bytes) else str(raw)
        try:
            started = datetime.datetime.strptime(raw, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            started = None
        run.check(started is not None
                  and wall_start <= started <= datetime.datetime.now(),
                  f"startTime {raw!r} is MATLAB's datetime format and falls "
                  f"within this run (started {wall_start:%H:%M:%S})")

        meta = dict(f["metadata"].attrs) if "metadata" in f else {}
        missing = sorted(METADATA_FIELDS - set(meta))
        run.check(not missing, f"metadata has all {len(METADATA_FIELDS)} fields "
                               f"(missing {missing})")
        if "frames_dropped_queue" in meta:
            run.check(int(meta["frames_dropped_queue"]) == w._scos_worker.dropped_count,
                      f"metadata.frames_dropped_queue = {meta['frames_dropped_queue']} "
                      f"(GUI counter {w._scos_worker.dropped_count})")
        if "gain_du_per_e" in meta:
            g_file, g_gui = float(meta["gain_du_per_e"]), float(w.processor.gain_du_per_e)
            run.check(g_file > 0 and np.isclose(g_file, g_gui, rtol=1e-12),
                      f"metadata.gain_du_per_e = {g_file:.6g} (processor {g_gui:.6g})")
        if "time_source" in meta:
            expected_src = str(getattr(cam, "time_source", "pc"))
            run.check(str(meta["time_source"]) == expected_src,
                      f"metadata.time_source = {meta['time_source']!r} "
                      f"(camera says {expected_src!r})")


def check_dialogs(run: Run) -> None:
    print("\nChecks — dialogs (in the order they opened)")
    for kind, title in run.dialogs:
        print(f"       {kind:22s} {title}")
    order = [t for _, t in run.dialogs if t != T_GAIN_WARNING]
    run.check(order == EXPECTED_DIALOGS,
              f"protocol order: {' → '.join(EXPECTED_DIALOGS)}")
    crit = [t for k, t in run.dialogs if k == "critical"]
    run.check(not crit, f"no error dialogs {crit or ''}")
    run.check(not run.unknown, f"no unexpected dialogs {run.unknown or ''}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    # The Windows console (and a pipe) defaults to cp1252, which has no κ, ≈ or →.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recording", type=Path, default=DEFAULT_RECORDING,
                    help="per-frame TIFF folder with a sibling <name>_dark folder")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal",
                    help="; ".join(f"{k}: {s.description}" for k, s in SCENARIOS.items()))
    ap.add_argument("--out", type=Path, default=None,
                    help="output root (default: a fresh temp folder; must be empty or absent)")
    ap.add_argument("--cal-frames", type=int, default=None,
                    help="dark and bright frame count (default: the committed config, "
                         "i.e. the protocol's 600)")
    ap.add_argument("--norm-seconds", type=int, default=3,
                    help="normalization window, s (default 3)")
    ap.add_argument("--measure-seconds", type=float, default=10.0,
                    help="measurement length after normalization, s of timeVec (default 10)")
    ap.add_argument("--timeout", type=float, default=1200.0,
                    help="watchdog: dump all stacks and exit after this many s (default 1200)")
    args = ap.parse_args()

    if args.measure_seconds <= args.norm_seconds:
        ap.error("--measure-seconds must be longer than --norm-seconds")
    if not args.recording.is_dir():
        ap.error(f"recording folder not found: {args.recording}")

    out = args.out or Path(tempfile.mkdtemp(prefix="scos_rehearsal_"))
    if out.exists() and any(out.iterdir()):
        ap.error(f"--out must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "rehearsal.log"

    # Same format as main.py, so lines compare directly with an app.log.
    # Everything goes to the file; only warnings and errors to the terminal.
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[logging.FileHandler(log_path, mode="w", encoding="utf-8"), console],
    )

    # Committed defaults only: a scos_config.local.json on this PC must not
    # change the run (same isolation as tests/conftest.py).
    cfg_dir = Path(tempfile.mkdtemp(prefix="scos_rehearsal_cfg_"))
    shutil.copy(_REPO / "scos_config.json", cfg_dir / "scos_config.json")
    os.environ["SCOS_CONFIG_DIR"] = str(cfg_dir)

    run = Run()

    # An exception escaping a Qt slot aborts a PyQt6 process with no summary;
    # with a hook installed PyQt calls it instead and carries on.
    def _hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        run.exceptions.append(text)
        logging.getLogger("rehearsal").error("Uncaught exception:\n%s", text)
    sys.excepthook = _hook

    faulthandler.dump_traceback_later(args.timeout, exit=True)

    print(f"scenario  : {args.scenario} — {SCENARIOS[args.scenario].description}")
    print(f"recording : {args.recording}")
    print(f"output    : {out}")
    print(f"log       : {log_path}\n")

    install_dialog_stubs(run, out)
    t0 = time.monotonic()
    try:
        rehearse(args, run)
    except Exception as exc:
        run.failures.append(f"rehearsal did not complete: {exc}")
        traceback.print_exc()
    finally:
        faulthandler.cancel_dump_traceback_later()

    check_dialogs(run)
    print("\nChecks — exceptions")
    run.check(not run.exceptions, f"no uncaught exceptions ({len(run.exceptions)})")
    for text in run.exceptions:
        print(text)

    print("\nFrames received by the GUI, by state / playback source:")
    for (state, src), count in sorted(run.frames.items()):
        print(f"       {state:15s} {src:5s} {count}")

    shutil.rmtree(cfg_dir, ignore_errors=True)
    print(f"\n{time.monotonic() - t0:.0f} s — ", end="")
    if run.failures:
        print(f"{len(run.failures)} FAILED:")
        for f in run.failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
