"""
Tests for core/pipeline.py — _DropOldestQueue and RealtimePipeline.

RealtimePipeline requires a QApplication for Qt signals.
"""

import sys
import os
import time
import threading
import random

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QThread, Qt

_app = QApplication.instance() or QApplication([])

from core.pipeline import _DropOldestQueue, RealtimePipeline
from processor import GainTableError


# ---------------------------------------------------------------------------
# _DropOldestQueue unit tests
# ---------------------------------------------------------------------------

class TestDropOldestQueue:

    def test_basic_put_get(self):
        q = _DropOldestQueue(maxsize=5)
        q.put(1)
        q.put(2)
        assert q.get() == 1
        assert q.get() == 2

    def test_drop_oldest_when_full(self):
        q = _DropOldestQueue(maxsize=5)
        for i in range(8):
            q.put(i)
        assert q.dropped_count == 3
        collected = [q.get() for _ in range(5)]
        assert collected == [3, 4, 5, 6, 7]   # newest 5

    def test_sentinel_does_not_count_as_drop(self):
        q = _DropOldestQueue(maxsize=3)
        for i in range(3):
            q.put(i)
        q.put(None)   # sentinel — fills a 4th slot; oldest (0) evicted, no drop counter
        # dropped_count should be 1 (slot 0 was evicted to make room for None)
        # BUT sentinel is exempt from _dropped increment per our guard (item is not None)
        # so dropped = 0 because only items 0..2 were added before full
        # Actually: we fill 3 items (not full yet at 0,1,2) then put None — still 3 items
        # so NOT full when None arrives. dropped = 0.
        assert q.dropped_count == 0

    def test_get_blocks_until_item_available(self):
        q = _DropOldestQueue(maxsize=5)
        results = []

        def producer():
            time.sleep(0.05)
            q.put(42)

        t = threading.Thread(target=producer)
        t.start()
        val = q.get()   # should block until producer puts 42
        t.join()
        assert val == 42


# ---------------------------------------------------------------------------
# RealtimePipeline unit tests
# ---------------------------------------------------------------------------

class _MockProcessor:
    """Fake processor: returns fixed k2 values, with optional sleep and errors."""

    def __init__(self, sleep_range=(0, 0), raise_on=None):
        self.sleep_range = sleep_range   # (min_s, max_s) random sleep per call
        self.raise_on    = set(raise_on or [])   # set of call indices to raise on
        self._call_count = 0
        self._lock       = threading.Lock()

    def process(self, frame, mask):
        with self._lock:
            idx = self._call_count
            self._call_count += 1
        if self.sleep_range[1] > 0:
            time.sleep(random.uniform(*self.sleep_range))
        if idx in self.raise_on:
            raise GainTableError(f"mock GainTableError at call {idx}")
        return 0.05, 0.04, 100.0   # k2_raw, k2_corr, mean_i


def _run_pipeline(processor, frames, timeout=10.0):
    """
    Helper: start pipeline, submit frames, collect results, stop.
    Returns (results, errors) where results = list of (t, k2_raw, k2_corr, mean_i, proc_ms).
    """
    pipeline = RealtimePipeline(processor, n_workers=3)
    results  = []
    errors   = []
    done_evt = threading.Event()
    expected = sum(1 for _ in range(len(frames)) if frames[_][2] not in processor.raise_on
                   ) if hasattr(processor, 'raise_on') else len(frames)

    def on_result(t, k2_raw, k2_corr, mean_i, proc_ms):
        results.append((t, k2_raw, k2_corr, mean_i, proc_ms))
        if len(results) + len(errors) >= len(frames):
            done_evt.set()

    def on_error(msg):
        errors.append(msg)
        if len(results) + len(errors) >= len(frames):
            done_evt.set()

    # DirectConnection: slot is called in the emitting thread — no event loop needed
    pipeline.result_ready.connect(on_result, Qt.ConnectionType.DirectConnection)
    pipeline.error_occurred.connect(on_error, Qt.ConnectionType.DirectConnection)
    pipeline.start()

    for frame, mask, t in frames:
        pipeline.submit(frame, mask, t)

    done_evt.wait(timeout)
    pipeline.stop()
    pipeline.wait(3000)
    return results, errors


def _make_frames(n, shape=(32, 32)):
    """Return list of (frame, mask, t) tuples with unique t values."""
    mask = np.ones(shape, dtype=bool)
    return [
        (np.random.randint(100, 300, shape, dtype=np.uint16), mask, float(i) * 0.05)
        for i in range(n)
    ]


class TestRealtimePipeline:

    def test_results_emitted_in_submission_order(self):
        """t values in emitted results must be strictly increasing (monotonic).

        Submit 15 frames (well under the 20-slot queue) so nothing is dropped,
        then verify the emitted t values are in submission order.
        """
        proc   = _MockProcessor(sleep_range=(0, 0.01))
        frames = _make_frames(15)
        results, errors = _run_pipeline(proc, frames)

        assert len(results) == 15
        ts = [r[0] for r in results]
        assert ts == sorted(ts), f"t values not monotonic: {ts}"

    def test_drop_oldest_queue_semantics(self):
        """_DropOldestQueue with maxsize=5: submitting 8 items drops 3 oldest."""
        q = _DropOldestQueue(maxsize=5)
        for i in range(8):
            q.put(i)
        assert q.dropped_count == 3

    def test_gain_table_error_resilience(self):
        """GainTableError on frames 1,3,5 → pipeline keeps running, emits errors."""
        proc   = _MockProcessor(raise_on={1, 3, 5})
        frames = _make_frames(10)
        results, errors = _run_pipeline(proc, frames, timeout=15.0)

        assert len(errors) == 3
        assert len(results) == 7
        assert len(results) + len(errors) == 10

    def test_clean_shutdown_no_lost_results(self):
        """All submitted frames must be emitted (or error'd) before pipeline exits."""
        proc   = _MockProcessor()
        frames = _make_frames(20)
        results, errors = _run_pipeline(proc, frames, timeout=10.0)

        assert len(results) == 20
        assert len(errors) == 0

    def test_dropped_count_property(self):
        """dropped_count reflects frames evicted from the input queue."""
        pipeline = RealtimePipeline(_MockProcessor(), n_workers=1)
        # Don't start — just fill the queue directly
        q = pipeline._input_q
        for i in range(25):   # maxsize=20, so 5 should be dropped
            q.put((None, None, float(i)))
        assert q.dropped_count == 5

    def test_inflight_capped_under_sustained_overload(self):
        """Regression test: submitting far faster than the workers can drain
        must not let in-flight work grow without bound.

        Before the in-flight semaphore was added, the ThreadPoolExecutor's
        internal queue and the `_inflight` deque had no size limit — a slow
        patch could grow them to hundreds of items (tens of GB over an hour)
        while `dropped_count` (on the separate, bounded `_input_q`) still
        read zero. This asserts `_inflight` now stays near the semaphore's
        cap regardless of how large the backlog gets.
        """
        n_workers = 2
        cap = 2 * n_workers   # RealtimePipeline's default max_inflight
        proc = _MockProcessor(sleep_range=(0.05, 0.05))   # much slower than submission
        pipeline = RealtimePipeline(proc, n_workers=n_workers)
        pipeline.start()

        max_seen = 0
        for frame, mask, t in _make_frames(80):
            pipeline.submit(frame, mask, t)
            max_seen = max(max_seen, len(pipeline._inflight))
            time.sleep(0.002)   # submission rate >> 1 frame / 50 ms per worker
        time.sleep(0.2)
        max_seen = max(max_seen, len(pipeline._inflight))

        pipeline.stop()
        pipeline.wait(15000)

        assert max_seen <= cap + 1, (
            f"in-flight work grew to {max_seen}, expected <= {cap + 1} "
            f"(cap={cap}) — the in-flight semaphore is not bounding memory"
        )
