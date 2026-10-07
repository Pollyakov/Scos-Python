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

What it can NOT test: the real modal dialogs. Every QMessageBox / QFileDialog
call is replaced by a stub that records it and answers the way an operator
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
SCENARIOS at the bottom: `normal` (3a) and `slowdown` (3b, see Slowdown);
rig prep steps 3c-3d add theirs there.
"""

import argparse
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
SESSION_FILES    = {"Calibration.h5", "rBfi_results.h5", "rBfi_fig.png"}

# Dialog titles, as MainWindow spells them.
T_GAIN_WARNING = "Estimated G[DU/e]"
T_FOLDER       = "Choose folder to save this session's results"
T_DARK_PROMPT  = "Calibration — Step 1 of 2: Dark Frames"
T_BRIGHT_PROMPT = "Calibration — Step 2 of 2: Bright Frames"
T_MEAS_ENDED   = "Measurement Ended"
T_LASER_ON     = "Laser May Still Be On"

# The protocol's order (docs/SCOS_protocol.md:11-17; CLAUDE.md "opening
# dialogs"). The G warning is optional — it appears only when the session's
# gain is not itself a row of the table, as 24 dB is not for this recording.
EXPECTED_DIALOGS = [T_FOLDER, T_DARK_PROMPT, T_BRIGHT_PROMPT, T_MEAS_ENDED]


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
        # Yes = "continue anyway": the session is saved, and the failed check
        # is still reported below through laser_off_result.
        ("question",    T_LASER_ON):      B.Yes,
        ("information", T_MEAS_ENDED):    B.Ok,
        ("warning",     T_GAIN_WARNING):  B.Ok,
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
        self.status_after:   str | None = None
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

    def during_measure(self, w, cam, args, pump) -> None:
        from PyQt6.QtCore import Qt

        def _arrived(t, *_rest):
            self.arrivals.append((time.monotonic(), t))
        w._scos_worker.result_ready.connect(_arrived, Qt.ConnectionType.QueuedConnection)

        def sample():
            now = time.monotonic()
            if now - self._last_sample >= 0.2:
                self._last_sample = now
                mem = _private_bytes()
                if mem is not None:
                    self.memory.append((now, mem, w._scos_worker.queue_depth, self.phase))
            if (self.overloads and self.status_after is None
                    and now >= self.overloads[0][0] + self.STATUS_AFTER_S):
                self.status_after = w.status.currentMessage()

        def wait(seconds, label):
            end = time.monotonic() + seconds
            pump(lambda: (sample(), time.monotonic() >= end)[1], seconds + 60, label)

        self.phase = "full"
        wait(self.FULL_S, f"{self.FULL_S:.0f} s at full speed ({cam.frame_rate:g} Hz playback)")

        self.phase = "slow"
        self.delay_s = self.SLOW_DELAY_S
        wait(self.SLOW_S, f"{self.SLOW_S:.0f} s slowed by {self.SLOW_DELAY_S} s/frame")
        self.dropped_before_stall = w._scos_worker.dropped_count

        self.phase = "stall"
        with self.lock:
            self.stall_s = self.STALL_S
        end = time.monotonic() + self.STALL_S + 30
        pump(lambda: (sample(), self.stall_ended is not None
                      or time.monotonic() > end)[1],
             self.STALL_S + 60, f"one {self.STALL_S:.0f} s stall")

        self.phase = "after"
        wait(self.AFTER_S, f"{self.AFTER_S:.0f} s slow again after the stall")
        self.dropped_after = w._scos_worker.dropped_count
        self.label_after   = w.lbl_dropped.text()
        if self.status_after is None:
            self.status_after = w.status.currentMessage()
        # Stop SCOS is pressed with the pipeline still slowed, as an operator
        # would press it on a machine that cannot keep up.

    def extra_checks(self, w, run: Run, folder, args) -> None:
        import h5py
        import numpy as np

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
        run.check("overload" in (self.status_after or "").lower(),
                  f"overload warning still on the status bar "
                  f"{self.STATUS_AFTER_S:.0f} s after it fired "
                  f"(shows {self.status_after!r})")
        run.check(pipe.error_count == 0, f"no processing errors ({pipe.error_count})")

        # timeVec against the stamps the frame source made, and the gaps
        # against the drop counter — the core of "keeps capture cadence".
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

        self._check_memory(w, run)

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
        for ph in ("full", "slow", "stall", "after"):
            mb = [m / 2**20 for _, m, _, p in self.memory if p == ph]
            if mb:
                print(f"       memory {ph:5s} {len(mb):3d} samples: median "
                      f"{np.median(mb):.0f}, min {min(mb):.0f}, max {max(mb):.0f} MB")

        def median_mb(keep) -> float | None:
            mb = [m / 2**20 for t, m, _, p in self.memory if keep(t, p)]
            return float(np.median(mb)) if mb else None

        full_mb  = median_mb(lambda t, p: p == "full")
        loaded   = [m for ph in ("slow", "stall", "after")
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
        after_mb = median_mb(lambda t, p: p == "after")
        rise     = (after_mb - full_q) if full_q is not None and after_mb is not None else None
        limit_mb = 10 * frame_mb
        run.check(rise is not None and rise <= limit_mb,
                  f"memory flat once the queue is full: "
                  f"{'n/a' if rise is None else f'{rise:+.0f} MB'} from the slow "
                  f"phase to the end, limit {limit_mb:.0f} MB (10 frames)")


_slowdown = Slowdown()

SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("one clean session at full speed"),
    "slowdown": Scenario(
        f"processing slowed, then stalled once, during the measurement "
        f"(playback at {Slowdown.PLAYBACK_HZ:g} Hz)",
        before_start=_slowdown.before_start,
        during_measure=_slowdown.during_measure,
        extra_checks=_slowdown.extra_checks,
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
    # message, which K1 overwrites — so capture its return value directly.
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
        verify_outputs(run, folder, cam, args)
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


def verify_outputs(run: Run, folder: Path | None, cam, args) -> None:
    """What the session must have left on disk (rig prep 3e extends this)."""
    import h5py
    import numpy as np
    from core.session import choose_norm_method

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
