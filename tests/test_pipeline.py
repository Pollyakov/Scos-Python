"""
Tests for core/pipeline.py — _DropOldestQueue and RealtimePipeline.

RealtimePipeline requires a QApplication for Qt signals.
"""

import sys
import os
import time
import threading
import random
import weakref
from typing import NamedTuple

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

    def __init__(self, sleep_range=(0, 0), raise_on=None, raise_generic_on=None):
        self.sleep_range      = sleep_range   # (min_s, max_s) random sleep per call
        self.raise_on         = set(raise_on or [])           # -> GainTableError
        self.raise_generic_on = set(raise_generic_on or [])   # -> plain RuntimeError
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
        if idx in self.raise_generic_on:
            raise RuntimeError(f"mock unexpected error at call {idx}")
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

    def test_generic_exception_counted_not_silent(self):
        """A generic (non-GainTableError) exception must not vanish without a
        trace: it should be counted via error_count, and the pipeline must
        keep processing frames submitted after it (regression test for the
        bare `except Exception: continue` that used to swallow it)."""
        proc = _MockProcessor(raise_generic_on={2})
        pipeline = RealtimePipeline(proc, n_workers=1)   # deterministic order
        results = []
        pipeline.result_ready.connect(
            lambda t, k2_raw, k2_corr, mean_i, proc_ms: results.append(t),
            Qt.ConnectionType.DirectConnection,
        )
        pipeline.start()

        for frame, mask, t in _make_frames(5):
            pipeline.submit(frame, mask, t)

        pipeline.stop()
        pipeline.wait(5000)

        assert pipeline.error_count == 1
        assert len(results) == 4   # the other 4 frames still come through

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


# ---------------------------------------------------------------------------
# Sustained-overload memory test (merged_worklist task 4)
# ---------------------------------------------------------------------------
#
# What "memory" means here
# ------------------------
# These tests measure memory as *retained-frame bytes*: a weakref is held to
# every frame handed to the pipeline, and the number still alive is counted.
# Because CPython frees a frame the moment the last reference drops, the alive
# count is exactly the number of frames the pipeline is still holding — in the
# input queue, in the executor's internal queue, or inside a worker.
#
# This is deliberately *not* resident-set-size (RSS) sampling. RSS is noisy:
# allocators keep freed pages, the GC runs when it likes, and other tests in the
# session move the number around — an RSS assertion would be either flaky or so
# loose it proves nothing. The alive count is exact, deterministic, and measures
# precisely the leak Review B described (frames parked in an unbounded executor
# queue). Multiplying by frame size converts it to real bytes; the frames are
# the lab's true 700x700 uint16 size, so the MB figure is the real one.
#
# Determinism
# -----------
# `_BlockingProcessor` never completes a frame until released, so "the workers
# are slower than the camera" is absolute rather than a race between a sleep and
# a submit loop. Nothing here depends on OS sleep granularity or thread
# scheduling luck, which is what makes the numbers reproducible on any machine.

_INFLIGHT_SLACK = 8   # frames in transit in thread locals at the moment we sample


class _BlockingProcessor:
    """Processor whose every call blocks until `release()` is called.

    Models a fully stalled processing stage — the worst case of the slowdown
    that motivated the in-flight cap — without depending on sleep timing.
    """

    def __init__(self) -> None:
        self._gate = threading.Event()

    def process(self, frame, mask):
        self._gate.wait(timeout=30)   # timeout only so a bug can't hang the suite
        return 0.05, 0.04, 100.0

    def release(self) -> None:
        self._gate.set()


class _OverloadStats(NamedTuple):
    submitted:      int
    max_alive:      int     # peak frames retained by the pipeline
    retained_mb:    float   # peak retained frames converted to megabytes
    max_inflight:   int     # peak len(pipeline._inflight)
    dropped:        int
    results:        int
    clean_exit:     bool
    q_maxsize:      int     # read from the pipeline, not hardcoded — see below


def _run_overload(
    *,
    max_inflight: int | None,
    n_workers: int = 2,
    n_frames: int = 60,
    shape: tuple[int, int] = (700, 700),
) -> _OverloadStats:
    """Flood a stalled pipeline with `n_frames` and measure what it retains.

    `max_inflight=None` uses the shipped default cap (2 * n_workers). Passing a
    huge value disables the cap, reproducing the pre-fix behaviour — see
    `test_uncapped_pipeline_leaks_silently` for why that matters.
    """
    proc     = _BlockingProcessor()
    pipeline = RealtimePipeline(proc, n_workers=n_workers, max_inflight=max_inflight)

    n_results = 0

    def on_result(*_args):
        nonlocal n_results
        n_results += 1

    pipeline.result_ready.connect(on_result, Qt.ConnectionType.DirectConnection)
    pipeline.start()

    mask         = np.ones(shape, dtype=bool)
    frame_nbytes = shape[0] * shape[1] * np.dtype(np.uint16).itemsize
    refs: list[weakref.ref] = []
    max_alive    = 0
    max_inflight_seen = 0

    for i in range(n_frames):
        # np.full (not np.zeros): zeros can come from calloc and never commit
        # pages, which would make the megabyte figure notional rather than real.
        frame = np.full(shape, 200, dtype=np.uint16)
        refs.append(weakref.ref(frame))
        pipeline.submit(frame, mask, float(i) * 0.05)
        del frame   # only the pipeline may hold this frame from here on
        max_alive         = max(max_alive, sum(1 for r in refs if r() is not None))
        max_inflight_seen = max(max_inflight_seen, len(pipeline._inflight))
        # Guarantee the dispatcher a scheduling window every iteration (~30 ms
        # total). Without it this is a tight pure-Python loop and the dispatcher
        # only runs when the GIL switch interval (5 ms default) preempts us — on
        # a loaded machine it could be starved for the whole loop, overflowing
        # _input_q and permanently losing the frames the uncapped control needs
        # to observe parked in memory. Costs nothing in the capped case, where
        # the dispatcher blocks on the semaphore after ~4 frames regardless.
        time.sleep(0.0005)

    # Let the dispatcher finish moving whatever it can out of _input_q, so the
    # peak reflects a settled backlog rather than a half-drained one.
    time.sleep(0.3)
    max_alive         = max(max_alive, sum(1 for r in refs if r() is not None))
    max_inflight_seen = max(max_inflight_seen, len(pipeline._inflight))

    proc.release()
    pipeline.stop()
    clean = pipeline.wait(20000)

    return _OverloadStats(
        submitted    = n_frames,
        max_alive    = max_alive,
        retained_mb  = max_alive * frame_nbytes / 1e6,
        max_inflight = max_inflight_seen,
        dropped      = pipeline.dropped_count,
        results      = n_results,
        clean_exit   = clean,
        q_maxsize    = pipeline._input_q._dq.maxlen,
    )


class TestSustainedOverload:
    """Task 4 of docs/reviews/merged_worklist.md.

    The pre-existing `test_inflight_capped_under_sustained_overload` covers only
    the item-count half of the requirement. These two cover the rest: that
    *memory* stays bounded and that drops are *counted*, in the same overload
    run — and, via the negative control, that the test would actually notice if
    the in-flight cap were removed again.
    """

    def test_memory_bounded_and_drops_counted_under_overload(self):
        """Frames arriving far faster than a stalled processor can drain them
        must leave memory flat and the loss visible in `dropped_count`."""
        n_workers = 2
        cap       = 2 * n_workers          # the shipped default max_inflight
        stats     = _run_overload(max_inflight=None, n_workers=n_workers)

        assert stats.clean_exit, "pipeline did not shut down in time — numbers below are meaningless"

        # Retained frames: the bounded _input_q, plus the capped in-flight set,
        # plus a small allowance for frames in transit in thread locals. The
        # queue size is read back from the pipeline rather than hardcoded, so
        # changing _input_q's size in core/pipeline.py cannot leave this bound
        # silently wrong in either direction.
        bound = stats.q_maxsize + cap + _INFLIGHT_SLACK
        assert stats.max_alive <= bound, (
            f"pipeline retained {stats.max_alive} frames "
            f"({stats.retained_mb:.1f} MB) out of {stats.submitted} submitted; "
            f"expected <= {bound} (queue {stats.q_maxsize} + in-flight cap {cap} "
            f"+ slack {_INFLIGHT_SLACK}). Memory is not bounded under sustained overload."
        )
        assert stats.max_inflight <= cap + 1, (
            f"in-flight work reached {stats.max_inflight}, expected <= {cap + 1}"
        )

        # The whole point of the design: the backlog is absorbed by the queue
        # that *counts* what it discards, so overload is visible to the operator.
        assert stats.dropped > 0, (
            "no drops were counted even though the processor never completed a "
            "frame — the backlog went somewhere invisible"
        )

        # Every submitted frame is accounted for: emitted, or counted as dropped.
        # The one permitted shortfall is the stop() sentinel: appending it to a
        # full deque evicts a frame without incrementing the counter (the
        # `item is not None` guard in _DropOldestQueue.put skips the increment,
        # but deque(maxlen=...) evicts anyway). Harmless at shutdown, but it
        # means exact equality would be wrong here.
        accounted = stats.results + stats.dropped
        assert stats.submitted - 1 <= accounted <= stats.submitted, (
            f"{stats.submitted} frames submitted but only {accounted} accounted "
            f"for ({stats.results} emitted + {stats.dropped} dropped) — "
            "frames are disappearing without being counted"
        )

    def test_uncapped_pipeline_leaks_silently(self):
        """Negative control: with the in-flight cap removed, the same run parks
        nearly every frame in memory while `dropped_count` still reads zero.

        This is the failure Review B reported, and it is what the test above
        exists to catch. Keeping it as a test rather than a one-off manual check
        means the cap cannot be removed — nor the bound above quietly widened —
        without a test turning red.
        """
        n_workers = 2
        stats     = _run_overload(max_inflight=10**6, n_workers=n_workers)

        assert stats.clean_exit, "pipeline did not shut down in time"

        # Same bound the capped test asserts, derived the same way.
        bound = stats.q_maxsize + 2 * n_workers + _INFLIGHT_SLACK
        assert stats.max_alive > bound, (
            f"expected the uncapped pipeline to retain more than {bound} frames, "
            f"but it retained {stats.max_alive} — this control no longer "
            "reproduces the unbounded-growth bug, so the test above proves nothing"
        )
        # ...and it does so invisibly: the drop counter never fires, because the
        # backlog bypassed the bounded queue entirely and piled up in the
        # executor's own unbounded queue.
        assert stats.dropped <= 1, (
            f"expected ~0 counted drops in the uncapped case, got {stats.dropped}"
        )
