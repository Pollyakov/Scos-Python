"""
Tests for core/frame_clock.py — capture times from the camera's own clock and
lost frames from its frame numbers (todo D5, rig prep 3b follow-up).

Synthetic cameras: exposures every 50 ms (20 Hz, the lab rate), each retrieved
a few ms later by the "PC", sometimes much later (a backlog in Pylon's buffers).
"""

import logging

import pytest

from core.frame_clock import FrameClock

FPS = 20.0
DT = 1.0 / FPS
PC_T0 = 1000.0          # monotonic seconds at the first exposure
LATENCY = 0.004         # normal exposure → retrieval delay


def frames(n, *, hz=1e9, latency=LATENCY, start_tick=123_456, first_block=1):
    """(t_retrieved, ticks, block_id, t_exposed) for n consecutive frames."""
    out = []
    for i in range(n):
        t_exp = PC_T0 + i * DT
        out.append((t_exp + latency, start_tick + round(i * DT * hz), first_block + i, t_exp))
    return out


def feed(clock, rows):
    return [clock.stamp(t, k, b) for t, k, b, _ in rows]


# ---------------------------------------------------------------------------
# Lost frames from BlockID
# ---------------------------------------------------------------------------

class TestLostFrames:

    def test_consecutive_ids_lose_nothing(self):
        c = FrameClock()
        assert [s.lost_before for s in feed(c, frames(10))] == [0] * 10
        assert c.frames_lost == 0

    def test_gap_is_counted_on_the_frame_after_it(self):
        c = FrameClock()
        rows = [r for r in frames(10) if r[2] not in (4, 5, 8)]
        lost = [s.lost_before for s in feed(c, rows)]
        assert lost == [0, 0, 0, 2, 0, 1, 0]
        assert c.frames_lost == 3

    def test_gige_wrap_is_not_a_loss(self):
        c = FrameClock()
        for b in (65534, 65535, 1, 2):
            c.stamp(PC_T0, 1, b)
        assert c.frames_lost == 0

    def test_loss_across_the_wrap_is_counted(self):
        c = FrameClock()
        c.stamp(PC_T0, 1, 65534)
        assert c.stamp(PC_T0, 2, 2).lost_before == 2      # 65535 and 1 missing
        assert c.frames_lost == 2

    def test_counter_restart_is_not_a_loss(self, caplog):
        c = FrameClock()
        c.stamp(PC_T0, 1, 500)
        with caplog.at_level(logging.INFO, logger="core.frame_clock"):
            assert c.stamp(PC_T0, 2, 1).lost_before == 0
        assert c.frames_lost == 0
        assert "restarted" in caplog.text

    def test_extended_64_bit_ids_pass_65535_without_a_wrap(self):
        c = FrameClock()
        for b in (65534, 65535, 65536, 65538):
            c.stamp(PC_T0, 1, b)
        assert c.frames_lost == 1                          # 65537

    @pytest.mark.parametrize("invalid", [0, 2**64 - 1])
    def test_unusable_ids_count_nothing_and_break_nothing(self, invalid):
        c = FrameClock()
        c.stamp(PC_T0, 1, 5)
        assert c.stamp(PC_T0, 2, invalid).lost_before == 0
        assert c.stamp(PC_T0, 3, 6).lost_before == 0       # 5 → 6, still tracked
        assert c.frames_lost == 0

    def test_failed_grab_counted_once_with_block_ids(self):
        c = FrameClock()
        c.stamp(PC_T0, 1, 1)
        assert c.count_failed_grab() == 0                  # block 2 failed…
        assert c.stamp(PC_T0, 3, 3).lost_before == 1       # …and the gap counts it
        assert c.frames_lost == 1

    def test_failed_grab_counted_directly_without_block_ids(self):
        c = FrameClock()
        assert c.count_failed_grab() == 1
        assert c.frames_lost == 1

    def test_reset_keeps_the_running_total(self):
        c = FrameClock()
        c.stamp(PC_T0, 1, 1)
        c.stamp(PC_T0, 2, 4)                               # 2 lost
        c.request_reset()
        c.stamp(PC_T0, 3, 1)                               # restart: not a loss
        assert c.frames_lost == 2


# ---------------------------------------------------------------------------
# Capture time from the camera clock
# ---------------------------------------------------------------------------

class TestCameraClock:

    def test_pc_time_until_checked_then_camera_time(self):
        c = FrameClock()
        rows = frames(200)                                 # 10 s
        stamps = feed(c, rows)
        n_check = int(FrameClock.VALIDATE_S * FPS)         # first 5 s
        for s, (t_ret, *_rest) in zip(stamps[:n_check], rows):
            assert s.t_capture == t_ret                    # PC stamp while checking
        assert c.time_source == "camera"
        # After acceptance: exposure time, on the PC scale (offset = latency).
        for s, (*_, t_exp) in zip(stamps[n_check + 1:], rows[n_check + 1:]):
            assert s.t_capture == pytest.approx(t_exp + LATENCY, abs=1e-9)

    def test_backlog_does_not_bunch_capture_times(self):
        """The 3b finding: frames retrieved in a burst after a stall must keep
        their exposure spacing, not the burst's."""
        c = FrameClock()
        rows = frames(240)                                 # 12 s
        # Stall: frames 160-179 (1 s) all retrieved together at 9.05 s + 2 ms each.
        burst = PC_T0 + 9.05
        rows = [(burst + 0.002 * (i - 160), k, b, te) if 160 <= i < 180 else (t, k, b, te)
                for i, (t, k, b, te) in enumerate(rows)]
        stamps = feed(c, rows)
        assert c.time_source == "camera"
        times = [s.t_capture for s in stamps[150:200]]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert gaps == pytest.approx([DT] * len(gaps), abs=1e-9)
        # What the PC alone would have said — the bunching that is now avoided.
        pc_gaps = [rows[i + 1][0] - rows[i][0] for i in range(160, 179)]
        assert max(pc_gaps) < 0.01

    def test_offset_comes_from_the_least_delayed_frame(self):
        c = FrameClock()
        rows = frames(200, latency=0.010)
        rows[30] = (rows[30][0] - 0.008, *rows[30][1:])    # one frame only 2 ms late
        stamps = feed(c, rows)
        assert stamps[-1].t_capture == pytest.approx(rows[-1][3] + 0.002, abs=1e-9)
        assert all(s.t_capture <= r[0] + 1e-12 for s, r in zip(stamps, rows))

    def test_125_mhz_camera_is_recognised(self):
        c = FrameClock()
        feed(c, frames(200, hz=125e6))
        assert c.time_source == "camera"

    def test_reported_tick_rate_is_used_and_checked(self):
        c = FrameClock(tick_hz=125e6)
        feed(c, frames(200, hz=125e6))
        assert c.time_source == "camera"

    def test_wrong_reported_tick_rate_falls_back_to_pc(self, caplog):
        c = FrameClock(tick_hz=1e9)                        # says ns, ticks 125 MHz
        rows = frames(200, hz=125e6)
        with caplog.at_level(logging.WARNING, logger="core.frame_clock"):
            stamps = feed(c, rows)
        assert c.time_source == "pc"
        assert "disagree" in c.unusable_reason
        assert [s.t_capture for s in stamps] == [r[0] for r in rows]
        assert "Camera clock not used" in caplog.text

    def test_unknown_tick_rate_is_never_guessed(self):
        c = FrameClock()
        rows = frames(200, hz=10e6)                        # not a Basler rate
        stamps = feed(c, rows)
        assert c.time_source == "pc"
        assert [s.t_capture for s in stamps] == [r[0] for r in rows]

    def test_camera_without_timestamps_uses_pc(self):
        c = FrameClock()
        rows = [(t, 0, b, te) for t, _, b, te in frames(200)]
        stamps = feed(c, rows)
        assert c.time_source == "pc"
        assert "no timestamps" in c.unusable_reason
        assert [s.t_capture for s in stamps] == [r[0] for r in rows]

    def test_clock_going_backwards_falls_back_to_pc_for_good(self):
        c = FrameClock()
        rows = frames(200)
        feed(c, rows)
        assert c.time_source == "camera"
        t, k, b, _ = rows[-1]
        s = c.stamp(t + DT, k - 10, b + 1)
        assert s.t_capture == t + DT
        assert c.time_source == "pc"
        assert "backwards" in c.unusable_reason
        c.request_reset()
        feed(c, frames(200, start_tick=10**12))
        assert c.time_source == "pc"                       # a fault, not a restart

    def test_grab_restart_rechecks_the_clock(self):
        c = FrameClock()
        feed(c, frames(200))
        assert c.time_source == "camera"
        c.request_reset()
        later = [(t + 100, k + 10**11, b, te + 100) for t, k, b, te in frames(50)]
        stamps = feed(c, later)
        assert c.time_source == "pc"                       # 2.5 s: still checking
        assert [s.t_capture for s in stamps] == [r[0] for r in later]


class TestClockCheckIsRobust:
    """A good camera must not be rejected because of one late frame or a
    coarse PC clock — rejection would silently switch the fix off."""

    def test_late_first_frame_does_not_reject_a_good_camera(self):
        c = FrameClock()
        rows = frames(200)
        rows[0] = (rows[0][0] + 0.100, *rows[0][1:])       # first retrieval 100 ms late
        stamps = feed(c, rows)
        assert c.time_source == "camera"
        assert stamps[-1].t_capture == pytest.approx(rows[-1][3] + LATENCY, abs=1e-9)

    def test_jitter_and_coarse_windows_clock_are_tolerated(self):
        """Up to 30 ms of random retrieval delay, and time.monotonic() rounded
        to the 15.625 ms ticks it has on Windows before Python 3.13."""
        import math
        import random
        rng = random.Random(7)
        tick = 1 / 64

        def coarse(t):
            return math.floor(t / tick) * tick

        rows = [(coarse(te + LATENCY + rng.uniform(0, 0.030)), k, b, te)
                for _, k, b, te in frames(200)]
        feed(c := FrameClock(), rows)
        assert c.time_source == "camera"

    def test_wrong_rate_is_still_rejected_under_the_same_noise(self):
        import math
        import random
        rng = random.Random(7)
        tick = 1 / 64
        rows = [(math.floor((te + LATENCY + rng.uniform(0, 0.030)) / tick) * tick, k, b, te)
                for _, k, b, te in frames(200, hz=125e6)]
        feed(c := FrameClock(tick_hz=1e9), rows)
        assert c.time_source == "pc"

    def test_missing_frame_numbers_are_logged_once(self, caplog):
        c = FrameClock()
        with caplog.at_level(logging.WARNING, logger="core.frame_clock"):
            for i in range(5):
                c.stamp(PC_T0 + i * DT, 0, 2**64 - 1)      # as pylon's emulator reports
        assert caplog.text.count("no frame numbers") == 1
