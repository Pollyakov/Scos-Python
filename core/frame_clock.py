"""
FrameClock — capture time and lost-frame count from the camera's own data.

Two things the PC cannot see by itself (todo D5, found in rig prep 3b):

* **When a frame was exposed.** `camera.py` used to stamp a frame with
  `time.monotonic()` right after `RetrieveResult()`, i.e. when the frame left
  Pylon's buffer. With no backlog that is milliseconds after exposure. Under
  overload it is not: frames that waited in the 20 buffers during a stall are
  retrieved in a burst and would get bunched stamps. Every grab result carries
  the camera's tick count at the start of exposure (`TimeStamp`); converted to
  seconds, it is the true capture time whatever the PC was doing.

* **Which frames never arrived.** Under `GrabStrategy_OneByOne`,
  `GetNumberOfSkippedImages()` does not count frames lost because every buffer
  was full (Basler's pylon API reference). Each grab result also carries a
  frame number (`BlockID`); a jump in it is a lost frame.

The camera clock is used only after it has been checked against the PC clock:
for the first VALIDATE_S seconds after (re)start, frames keep the PC stamp,
and the camera's elapsed time is compared with the PC's. It is accepted only
if the two agree within TOLERANCE for one of the plausible tick frequencies.
Otherwise — a camera without timestamps, or a tick frequency that does not
fit — the PC stamp stays, for good, and `time_source` says so. A wrong tick
frequency would stretch the whole timeVec, so guessing is never allowed.

The check runs while the camera previews; a measurement starts only after the
dark and bright calibrations, far later than VALIDATE_S. So within one
measurement the source does not change, except in the one case handled below
(the camera clock going backwards), which falls back to the PC and says so.

Pure Python, no pypylon: `camera.py` feeds it the three numbers per frame.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# GigE block IDs (extended ID mode off, the default) run 1 … 65535, then 1 again.
_GIGE_BLOCK_ID_MAX = 65535
# A block ID equal to UINT64_MAX is invalid; USB cameras report 0 when the
# feature is unsupported (pylon API reference, CGrabResultData::GetBlockID).
_BLOCK_ID_INVALID = 2**64 - 1
# Below this distance from the top a drop back to a small ID is a wrap; anywhere
# else it means the counter restarted (grabbing restarted) — not a loss.
_WRAP_MARGIN = 1000

# Tick frequencies Basler cameras use: 1 GHz (ace 2, SFNC 2.x — timestamps in
# ns) and 125 MHz (ace classic GigE). Used only when the camera does not report
# its own frequency.
DEFAULT_TICK_CANDIDATES_HZ = (1e9, 125e6)


@dataclass(frozen=True)
class Stamp:
    t_capture:  float   # seconds, on the time.monotonic() scale
    lost_before: int    # frames missing between the previous result and this one


class FrameClock:
    """Per-frame capture time and lost-frame count. Not thread-safe: one
    owner (the camera thread), plus `reset()` requests via `request_reset()`."""

    VALIDATE_S = 5.0     # PC-clock span the camera clock is checked over
    EDGE_S     = 1.0     # first / last second of it: where the minima are taken
    TOLERANCE  = 0.01    # 1 % of ≈ 4 s ≈ 40 ms; least-delayed frames vary by far less

    def __init__(self, tick_hz: float | None = None) -> None:
        # The camera's reported frequency if it has one, else the candidates.
        self._candidates = (tick_hz,) if tick_hz else DEFAULT_TICK_CANDIDATES_HZ
        self.frames_lost = 0
        self.time_source = "pc"          # "pc" until validated, then "camera"
        self._unusable_reason: str | None = None
        self._warned_no_block_ids = False
        self._reset_requested = False
        self.reset()

    # ------------------------------------------------------------------
    def request_reset(self) -> None:
        """Ask for reset() before the next frame — safe from another thread
        (one bool write). Call whenever grabbing is stopped and restarted."""
        self._reset_requested = True

    def reset(self) -> None:
        """Forget the previous frame: block IDs and the clock check start over.
        `frames_lost` is a running total and is kept."""
        self._last_block: int | None = None
        self._samples: list[tuple[float, int]] = []    # (PC time, ticks) while checking
        self._tick_hz: float | None = None
        self._offset = 0.0                              # PC − camera seconds
        self._last_ticks: int | None = None
        if self._unusable_reason is None:
            self.time_source = "pc"

    # ------------------------------------------------------------------
    def stamp(self, t_retrieved: float, ticks: int, block_id: int) -> Stamp:
        """One grab result → its capture time and the frames lost before it.

        t_retrieved: time.monotonic() right after RetrieveResult()
        ticks:       grab result TimeStamp (0 = camera has none)
        block_id:    grab result BlockID
        """
        if self._reset_requested:
            self._reset_requested = False
            self.reset()
        lost = self._count_lost(block_id)
        return Stamp(self._time(t_retrieved, ticks), lost)

    def count_failed_grab(self) -> int:
        """A grab result that arrived incomplete. Returns how many frames this
        adds to `frames_lost`: 0 while block IDs are being tracked (the next
        good frame's gap includes it), else 1."""
        if self._last_block is not None:
            return 0
        self.frames_lost += 1
        return 1

    # ------------------------------------------------------------------
    def _count_lost(self, block_id: int) -> int:
        if not block_id or block_id == _BLOCK_ID_INVALID:
            if not self._warned_no_block_ids:
                self._warned_no_block_ids = True
                logger.warning("Camera reports no frame numbers (BlockID %d) — "
                               "frames lost before reaching the app cannot be "
                               "counted", block_id)
            return 0
        last, self._last_block = self._last_block, block_id
        if last is None:
            return 0
        if block_id > last:
            lost = block_id - last - 1
        elif block_id <= _WRAP_MARGIN and last >= _GIGE_BLOCK_ID_MAX - _WRAP_MARGIN:
            lost = (_GIGE_BLOCK_ID_MAX - last) + (block_id - 1)    # wrapped
        else:
            logger.info("Camera frame counter restarted (%d after %d) — "
                        "not counted as lost frames", block_id, last)
            return 0
        self.frames_lost += lost
        return lost

    def _time(self, t_retrieved: float, ticks: int) -> float:
        if self._unusable_reason is not None or not ticks:
            if not ticks and self._unusable_reason is None:
                self._give_up("the camera reports no timestamps")
            return t_retrieved

        if self._last_ticks is not None and ticks < self._last_ticks:
            self._give_up("the camera clock went backwards "
                          f"({ticks} after {self._last_ticks})")
            return t_retrieved
        self._last_ticks = ticks

        if self.time_source == "camera":
            return ticks / self._tick_hz + self._offset

        # Still checking: keep the PC stamp and collect the evidence.
        self._samples.append((t_retrieved, ticks))
        t0 = self._samples[0][0]
        span_pc = t_retrieved - t0
        if span_pc < self.VALIDATE_S:
            return t_retrieved

        hz = self._best_candidate()
        if hz is None:
            self._give_up(
                f"its timestamps disagree with the PC clock by more than "
                f"{self.TOLERANCE:.0%} over {span_pc:.1f} s (tried "
                + ", ".join(f"{c / 1e6:g} MHz" for c in self._candidates) + ")")
            return t_retrieved
        self._tick_hz = hz
        self._offset  = min(t - k / hz for t, k in self._samples)
        self._samples = []
        self.time_source = "camera"
        logger.info("Camera clock accepted — %g MHz ticks agree with the PC "
                    "clock over %.1f s; capture times now come from the camera",
                    hz / 1e6, span_pc)
        return ticks / hz + self._offset

    def _best_candidate(self) -> float | None:
        """The candidate tick rate under which the camera and PC clocks keep
        the same distance from the start of the window to its end.

        Retrieval is always *after* exposure, so for the right rate the PC-
        minus-camera difference of every frame is (constant offset) + (that
        frame's delay). Taking the *smallest* difference in the first and in
        the last second picks the least delayed frame of each, so one late
        frame — or the 15.6 ms steps of time.monotonic() on Windows before
        Python 3.13 — cannot reject a good camera. For a wrong rate the two
        minima drift apart by (1 − ratio) × elapsed time: 87.5 % of it for
        1 GHz vs 125 MHz, far outside TOLERANCE.
        """
        t_first, t_last = self._samples[0][0], self._samples[-1][0]
        early = [(t, k) for t, k in self._samples if t <= t_first + self.EDGE_S]
        late  = [(t, k) for t, k in self._samples if t >= t_last - self.EDGE_S]
        elapsed = (sum(t for t, _ in late) / len(late)
                   - sum(t for t, _ in early) / len(early))
        if elapsed <= 0:
            return None
        for hz in self._candidates:
            drift = (min(t - k / hz for t, k in late)
                     - min(t - k / hz for t, k in early))
            if abs(drift) <= self.TOLERANCE * elapsed:
                return hz
        return None

    def _give_up(self, why: str) -> None:
        self._unusable_reason = why
        self.time_source = "pc"
        logger.warning("Camera clock not used — %s. Capture times are the PC "
                       "clock when each frame is retrieved (accurate unless "
                       "frames wait in Pylon's buffers under overload).", why)

    @property
    def unusable_reason(self) -> str | None:
        return self._unusable_reason
