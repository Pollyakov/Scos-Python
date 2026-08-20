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
"""

import collections
import concurrent.futures
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from processor import GainTableError, SCOSProcessor

if TYPE_CHECKING:
    pass


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
    """Thread-safe bounded queue that evicts the *oldest* item when full.

    Unlike queue.Queue (which raises Full or blocks), appending to a full
    queue here silently drops the head element so the most recent frame
    is always accepted.
    """

    def __init__(self, maxsize: int) -> None:
        self._dq      = collections.deque(maxlen=maxsize)
        self._cond    = threading.Condition()
        self._dropped = 0

    def put(self, item: object) -> None:
        """Non-blocking. Evicts oldest item if full (sentinel None never evicts)."""
        with self._cond:
            if item is not None and len(self._dq) == self._dq.maxlen:
                self._dropped += 1   # deque will auto-evict oldest on append
            self._dq.append(item)
            self._cond.notify()

    def get(self) -> object:
        """Blocking. Returns oldest item (or sentinel)."""
        with self._cond:
            while not self._dq:
                self._cond.wait()
            return self._dq.popleft()

    @property
    def dropped_count(self) -> int:
        return self._dropped


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

    Signals:
        result_ready(t, k2_raw, k2_corr, mean_i, proc_ms)
        error_occurred(message)
    """

    result_ready   = pyqtSignal(float, float, float, float, float)
    error_occurred = pyqtSignal(str)

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

    # ------------------------------------------------------------------
    # Public API (called from GUI thread)

    def submit(self, frame: np.ndarray, mask: np.ndarray, t: float) -> None:
        """Enqueue a frame for processing. Non-blocking; drops oldest if full."""
        self._input_q.put((frame, mask, t))

    @property
    def dropped_count(self) -> int:
        """Total frames evicted from the input queue due to backpressure."""
        return self._input_q.dropped_count

    def stop(self) -> None:
        """Signal the pipeline to drain and exit. Call before wait()."""
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
                fut = pool.submit(_worker_fn, frame, mask, t, self._processor)
                with self._inf_cond:
                    self._inflight.append(fut)
                    self._inf_cond.notify()
        # ThreadPoolExecutor.__exit__ calls shutdown(wait=True) — all futures
        # are resolved before we reach here.
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
            except GainTableError as e:
                self._inflight_sem.release()
                self.error_occurred.emit(str(e))
                continue
            except Exception:
                # Swallowed here deliberately for now — logging/counting this
                # path is a separate, already-planned follow-up task. Only the
                # semaphore release is added so in-flight capacity isn't leaked.
                self._inflight_sem.release()
                continue
            self._inflight_sem.release()
            self.result_ready.emit(t, k2_raw, k2_corr, mean_i, proc_ms)
