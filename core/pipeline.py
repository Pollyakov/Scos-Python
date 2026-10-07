"""
RealtimePipeline — drop-oldest 20-frame input queue + ThreadPoolExecutor for
parallel frame processing, with a bounded number of frames in flight.

Replaces the old 1-frame drop-newest _SCOSWorkerThread with proper pipeline
parallelism: N frames are in-flight concurrently (GIL released by NumPy/cv2),
results are emitted in submission order to keep the BFI time-series monotonic.

Backpressure design: `_input_q` is bounded and visible (drop-oldest, counted
via `dropped_count`), but the ThreadPoolExecutor's internal queue and the
`_inflight` deque behind it are not bounded by the library itself. A slow
processing stage could previously grow those two without limit — a hidden,
uncounted memory leak that OOMs a multi-hour session while `dropped_count`
still reads zero. `_inflight_sem` caps how many frames may be submitted to
the pool but not yet collected by the emitter; once that cap is reached the
dispatcher blocks (it runs on its own QThread, not the GUI thread), so any
further backlog is absorbed by the bounded, visible `_input_q` instead.

A frame whose processing raises (other than GainTableError, which is a
recognized, user-facing condition) is logged and counted via `error_count`
rather than silently discarded — an uncounted, unlogged drop is how a bug
like a data race turns into an invisible gap in the results.
"""

import collections
import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from processor import GainTableError, SCOSProcessor

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _worker_fn(
    frame: np.ndarray,
    mask: np.ndarray,
    t: float,
    processor: SCOSProcessor,
) -> tuple[float, float, float, float, float]:
    """Run in a thread-pool thread. Returns (t, k2_raw, k2_corr, mean_i, proc_ms).

    SCOSProcessor.process() is safe for concurrent calls once measurement
    starts — all shared state (dark_mean, dark_var, bright_var, ROI crops,
    gain) is read-only during measurement.

    Note: _cached_G has a benign CPython read-check-write pattern; the value
    is deterministic so concurrent writes always produce the same result.
    """
    t0 = time.perf_counter()
    k2_raw, k2_corr, mean_i = processor.process(frame, mask)
    proc_ms = (time.perf_counter() - t0) * 1000.0
    return t, k2_raw, k2_corr, mean_i, proc_ms


class _DropOldestQueue:
    """Thread-safe bounded queue with two producer modes.

    Non-blocking (``block=False``, the default): appending to a full queue
    evicts the head element and counts it, so the most recent frame is always
    accepted. Used by ``submit()``.

    Blocking (``block=True``): the producer *waits* for a free slot instead,
    which is what turns a slow processing stage into real backpressure on the
    camera thread rather than a silent loss (Implementation_Plan §2). The wait
    is always bounded: after ``timeout`` seconds the item is accepted anyway,
    evicting and counting the oldest. That hard cap matters — a producer that
    could wait forever would hang ``CameraThread.stop()``, which calls
    ``wait()`` with no timeout, and with it the whole GUI.
    """

    def __init__(self, maxsize: int) -> None:
        self._dq      = collections.deque(maxlen=maxsize)
        self._cond    = threading.Condition()
        self._dropped = 0

    def put(self, item: object, *, block: bool = False,
            timeout: float | None = None) -> bool:
        """Append an item. Returns True if it fit, False if it evicted one.

        A False return is exactly the "we lost a frame" case, and is always
        reflected in ``dropped_count`` (the sentinel None is the one exception
        — see Question Q7 in the merged worklist).
        """
        deadline = (time.monotonic() + timeout) if (block and timeout is not None) else None
        with self._cond:
            if block:
                while len(self._dq) == self._dq.maxlen:
                    if deadline is None:
                        self._cond.wait(0.05)
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break        # hard cap hit — fall through and evict
                    self._cond.wait(remaining)
            queued = True
            if item is not None and len(self._dq) == self._dq.maxlen:
                self._dropped += 1   # deque will auto-evict oldest on append
                queued = False
            self._dq.append(item)
            self._cond.notify_all()
            return queued

    def get(self) -> object:
        """Blocking. Returns oldest item (or sentinel)."""
        with self._cond:
            while not self._dq:
                self._cond.wait()
            item = self._dq.popleft()
            # A slot just freed up — wake any producer blocked in put().
            self._cond.notify_all()
            return item

    def clear(self) -> int:
        """Discard every queued frame; return how many. Wakes any producer
        blocked in put(), which then finds room. Not counted as dropped: used
        only when the results are no longer wanted (RealtimePipeline.stop)."""
        with self._cond:
            n = sum(1 for item in self._dq if item is not None)
            self._dq.clear()
            self._cond.notify_all()
            return n

    @property
    def dropped_count(self) -> int:
        return self._dropped

    @property
    def qsize(self) -> int:
        with self._cond:
            return len(self._dq)

    @property
    def maxsize(self) -> int:
        return self._dq.maxlen


@dataclass(frozen=True)
class _Intake:
    """Immutable snapshot of everything the camera-thread intake path needs.

    Published by the GUI thread with a single attribute assignment and read by
    the camera thread with a single attribute read - both atomic under the GIL,
    so the camera thread can never observe a half-updated intake state. Same
    pattern as processor._RoiCrop (merged worklist task 3).
    """
    mask:          np.ndarray
    put_timeout_s: float


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class RealtimePipeline(QThread):
    """Pipeline-parallel SCOS frame processor.

    Architecture:
      Dispatcher (QThread.run):  input_q → ThreadPoolExecutor → inflight deque
      Emitter   (daemon thread): inflight deque → future.result() → Qt signals

    Results are emitted in submission order (oldest future first) so the
    BFI time-series is always monotonically increasing.

    Frame intake runs on the *camera* thread, not the GUI thread: the camera's
    frame_ready signal is connected to on_frame() with a DirectConnection, so a
    full input queue blocks the grab loop (real backpressure, absorbed by the
    camera's own 20-frame Pylon buffer) instead of either freezing the GUI or
    piling frames up in Qt's unbounded queued-connection event queue.

    Signals:
        result_ready(t, k2_raw, k2_corr, mean_i, proc_ms)
        error_occurred(message)
        overload_detected(queue_depth)  - input queue first crossed 80 % full
    """

    result_ready      = pyqtSignal(float, float, float, float, float)
    error_occurred    = pyqtSignal(str)
    overload_detected = pyqtSignal(int)

    # Fraction of the input queue that counts as "overloaded", and the lower
    # fraction it must fall back to before the warning can fire again - a plain
    # threshold would re-fire on every frame while hovering at the mark.
    _OVERLOAD_HIGH = 0.8
    _OVERLOAD_LOW  = 0.5

    def __init__(
        self,
        processor: SCOSProcessor,
        n_workers: int = 3,
        *,
        max_inflight: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._processor = processor
        self._n_workers = n_workers
        self._input_q   = _DropOldestQueue(maxsize=20)
        self._inflight  : collections.deque = collections.deque()
        self._inf_cond  = threading.Condition()
        # Caps frames submitted-but-not-yet-collected. Default: enough for every
        # worker to have one task running plus one queued, so a worker never sits
        # idle waiting for the dispatcher — without letting the backlog grow
        # unbounded the way the bare ThreadPoolExecutor queue would.
        self._inflight_sem = threading.Semaphore(max_inflight or 2 * n_workers)
        self._error_count = 0   # frames whose processing raised — see _emit_loop
        self._discarding  = False   # stop(discard_queued=True) was called

        # Camera-thread intake state. `_intake` is None whenever no measurement
        # is running, which makes on_frame() a cheap early return during
        # PREVIEW and the two calibration phases.
        self._intake: _Intake | None = None
        self._t0_capture: float | None = None   # monotonic — the timeVec origin
        self._t0_wall:    float | None = None   # wall clock at that same instant
        self._overloaded = False

    # ------------------------------------------------------------------
    # Public API (called from GUI thread)

    def submit(self, frame: np.ndarray, mask: np.ndarray, t: float) -> None:
        """Enqueue a frame for processing. Non-blocking; drops oldest if full.

        This is the caller-supplies-its-own-timestamp path, kept for tests and
        for any producer that must never be blocked. The live camera path is
        on_frame(), which blocks instead of dropping.
        """
        self._input_q.put((frame, mask, t))

    def enable_intake(self, mask: np.ndarray, *,
                      put_timeout_s: float = 1.5) -> None:
        """Start accepting frames from the camera thread (call at MEASURING_INIT).

        `mask` is the shrunk SCOS mask; it is captured into an immutable bundle
        so the camera thread never reads it mid-update. The timeVec origin is
        latched from the first frame that actually arrives, not from this call,
        so t=0 is a real capture instant.

        Caller contract: this mask must stay in step with the one handed to
        `processor.set_roi()` — the workers read the processor's ROI crops, not
        this bundle, and the two describe the same region. Today that holds
        because ROI edits are locked for the whole of MEASURING_INIT/MEASURING
        (merged worklist task 3); anything that unlocks ROI mid-run has to
        re-publish both together.
        """
        self._t0_capture = None
        self._t0_wall    = None
        self._overloaded = False
        self._intake = _Intake(mask=mask, put_timeout_s=put_timeout_s)

    def disable_intake(self) -> None:
        """Stop accepting camera frames (call on Stop SCOS / FINISHED)."""
        self._intake = None

    @property
    def intake_enabled(self) -> bool:
        return self._intake is not None

    @property
    def t0_capture(self) -> float | None:
        """Monotonic capture time of the first measured frame - the t=0 origin."""
        return self._t0_capture

    @property
    def t0_wall(self) -> float | None:
        """Wall-clock time at t=0, for the absolute `startTime` the schema needs.

        Monotonic is the right clock for intervals (it cannot jump when the OS
        syncs time mid-recording) but carries no absolute meaning, so the two
        are latched together on the first frame.
        """
        return self._t0_wall

    def on_frame(self, frame: np.ndarray, t_capture: float) -> None:
        """Camera-thread intake slot - connect with Qt.ConnectionType.DirectConnection.

        Runs on whichever thread emitted frame_ready (the camera/mock thread),
        never the GUI thread. Blocks while the queue is full, which throttles
        the grab loop; the wait is capped inside put(), so a wedged pipeline
        stalls capture briefly and countably rather than forever.
        """
        intake = self._intake            # one atomic read - see _Intake
        if intake is None:
            return                       # not measuring: nothing to do

        if self._t0_capture is None:
            self._t0_capture = t_capture
            self._t0_wall    = time.time()
        t = t_capture - self._t0_capture

        self._input_q.put((frame, intake.mask, t),
                          block=True, timeout=intake.put_timeout_s)
        self._check_overload()

    def _check_overload(self) -> None:
        """Emit overload_detected once per episode, on the way up past 80 %."""
        maxsize = self._input_q.maxsize or 1
        depth   = self._input_q.qsize
        if not self._overloaded and depth >= self._OVERLOAD_HIGH * maxsize:
            self._overloaded = True
            self.overload_detected.emit(depth)
        elif self._overloaded and depth <= self._OVERLOAD_LOW * maxsize:
            self._overloaded = False

    @property
    def dropped_count(self) -> int:
        """Total frames evicted from the input queue due to backpressure."""
        return self._input_q.dropped_count

    @property
    def error_count(self) -> int:
        """Total frames whose processing raised an unexpected exception."""
        return self._error_count

    @property
    def queue_depth(self) -> int:
        """Frames waiting in the input queue (drives the task-18 fill bar)."""
        return self._input_q.qsize

    @property
    def queue_maxsize(self) -> int:
        return self._input_q.maxsize

    def stop(self, *, discard_queued: bool = False) -> None:
        """Signal the pipeline to exit. Call before wait().

        By default it drains first: every frame already submitted is processed
        and its result emitted (tests rely on that — no result is lost to a
        shutdown).

        discard_queued=True is for when nobody will use those results — the
        window closing, or Start SCOS replacing this pipeline (todo K5). The
        stop marker would otherwise sit *behind* up to 20 queued frames plus
        the in-flight ones, and on a machine that is far behind the thread
        outlived MainWindow.closeEvent's 2 s wait and was killed by Python's
        shutdown mid-task. Now the queued frames are dropped, frames handed to
        the pool but not started are cancelled, and only those already being
        processed finish — one process() call per worker at most.

        Intake is closed first: once the dispatcher exits nothing drains the
        input queue, so a camera thread still feeding a dead pipeline would
        block for the full put timeout on every frame.
        """
        self._intake = None
        if discard_queued:
            self._discarding = True
            n = self._input_q.clear()
            # Handed to the pool but not started: cancel now. Only from here —
            # the dispatcher may be blocked waiting for a slot, and a worker
            # that frees up would otherwise start the next one first.
            # Future.cancel() is thread-safe and refuses running futures.
            with self._inf_cond:
                n += sum(1 for f in self._inflight if f is not None and f.cancel())
            if n:
                logger.info("Pipeline stopping — %d frame(s) discarded unprocessed, "
                            "their results are no longer wanted", n)
        self._input_q.put(None)

    # ------------------------------------------------------------------
    # QThread entry point — dispatcher loop

    def run(self) -> None:
        emitter = threading.Thread(target=self._emit_loop, daemon=True)
        emitter.start()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self._n_workers
        ) as pool:
            while True:
                item = self._input_q.get()
                if item is None:
                    break
                frame, mask, t = item
                # Block here (dispatcher thread only — never the GUI thread)
                # until a slot frees up, instead of growing the pool's internal
                # queue and _inflight without limit. While blocked, new frames
                # keep arriving into the bounded _input_q, which drops the
                # oldest and counts it — visible backpressure instead of a
                # silent, unbounded memory leak.
                self._inflight_sem.acquire()
                if self._discarding:            # stop(discard_queued=True)
                    self._inflight_sem.release()
                    continue                    # the next get() is the sentinel
                fut = pool.submit(_worker_fn, frame, mask, t, self._processor)
                with self._inf_cond:
                    self._inflight.append(fut)
                    self._inf_cond.notify()
            if self._discarding:
                # Submitted but not started: cancel. Running ones finish.
                pool.shutdown(wait=True, cancel_futures=True)
        # ThreadPoolExecutor.__exit__ calls shutdown(wait=True) — all futures
        # are resolved (or cancelled) before we reach here.
        with self._inf_cond:
            self._inflight.append(None)   # sentinel to unblock emitter
            self._inf_cond.notify()
        emitter.join()

    # ------------------------------------------------------------------
    # Emitter loop — plain thread, preserves submission order

    def _emit_loop(self) -> None:
        while True:
            with self._inf_cond:
                while not self._inflight:
                    self._inf_cond.wait()
                item = self._inflight.popleft()
            if item is None:
                break
            try:
                t, k2_raw, k2_corr, mean_i, proc_ms = item.result()
            except concurrent.futures.CancelledError:
                # Cancelled by stop(discard_queued=True): not an error.
                self._inflight_sem.release()
                continue
            except GainTableError as e:
                self._inflight_sem.release()
                self.error_occurred.emit(str(e))
                continue
            except Exception:
                # The frame itself is unrecoverable — there's no k2/BFi to
                # emit — but the failure must not vanish without a trace:
                # that's how bugs like the ROI race turn into silent data
                # gaps. Log with traceback and count it instead.
                self._inflight_sem.release()
                self._error_count += 1
                logger.exception(
                    "SCOS worker raised an unexpected exception — frame "
                    "dropped (error #%d so far)", self._error_count,
                )
                continue
            self._inflight_sem.release()
            self.result_ready.emit(t, k2_raw, k2_corr, mean_i, proc_ms)
