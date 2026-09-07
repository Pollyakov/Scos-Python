"""
End-to-end test for frame intake — merged_worklist task 5.

Wires a real frame source (FolderMockCamera, on its own QThread) into
RealtimePipeline exactly the way gui/main_window.py does:

    cam.frame_ready --DirectConnection--> pipeline.on_frame   (camera thread)
    cam.frame_ready --queued-----------> _on_scos_frame        (GUI thread)

then stalls the GUI-thread handler well past the frame interval and checks
that the emitted timeVec still follows the *camera's* cadence.

Why this test exists on top of the unit tests in test_pipeline.py: those feed
on_frame() synthetic timestamps directly, so they prove the pipeline honours a
capture time it is handed. They cannot prove the wiring delivers one — that the
signal really is direct-connected onto the camera thread, and that a busy GUI
thread no longer sets the sampling rate. This test runs the actual objects,
with a real Qt event loop, and fails if either half regresses.

The failure being guarded against is not cosmetic: timestamps taken on a GUI
thread that is 80 ms/frame behind describe a 12.5 Hz recording when the camera
actually ran at 20 Hz. Downstream FFT-based pulse analysis reads that as the
wrong heart rate.
"""

import os
import statistics
import sys
import time

import numpy as np
import pytest
import tifffile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

_app = QApplication.instance() or QApplication([])

from folder_camera import FolderMockCamera
from core.pipeline import RealtimePipeline


N_FRAMES  = 20
FPS       = 20.0     # camera cadence: one frame every 50 ms
GUI_STALL = 0.150    # GUI handler cost per frame — 3x the frame interval
SHAPE     = (32, 32)


class _FastProcessor:
    """Processing is not what is under test here — keep it out of the timing."""

    def process(self, frame, mask):
        return 0.05, 0.04, 100.0


def _write_recording(folder, n=N_FRAMES):
    for i in range(n):
        tifffile.imwrite(
            str(folder / f"Basler_cam__1__20240101_000000000_{i:04d}.tiff"),
            np.full(SHAPE, 200 + i, dtype=np.uint16),
        )


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    """Run one stalled-GUI replay and return the timing it produced."""
    rec_dir = tmp_path_factory.mktemp("recording")
    _write_recording(rec_dir)

    cam = FolderMockCamera(str(rec_dir), loop=False)
    cam.frame_rate = FPS
    pipeline = RealtimePipeline(_FastProcessor(), n_workers=2)

    times      = []   # timeVec as emitted by the pipeline (capture-based)
    gui_stamps = []   # when the GUI thread actually reached each frame

    def on_scos_frame_gui(frame, t_capture):
        """Stand-in for MainWindow._on_scos_frame — deliberately slow."""
        gui_stamps.append(time.monotonic())
        time.sleep(GUI_STALL)

    pipeline.result_ready.connect(lambda t, *_: times.append(t))   # queued → GUI
    cam.frame_ready.connect(on_scos_frame_gui)                     # queued → GUI
    cam.frame_ready.connect(pipeline.on_frame,                     # → camera thread
                            Qt.ConnectionType.DirectConnection)

    pipeline.start()
    pipeline.enable_intake(np.ones(SHAPE, dtype=bool))
    cam.start_capture()

    deadline = time.monotonic() + 60.0
    timer    = QTimer()
    timer.timeout.connect(
        lambda: (len(times) >= N_FRAMES or time.monotonic() > deadline)
        and _app.quit()
    )
    timer.start(20)
    _app.exec()
    timer.stop()

    cam.stop()
    pipeline.stop()
    pipeline.wait(3000)

    return {
        "times":      times,
        "gui_stamps": gui_stamps,
        "cap_gaps":   [b - a for a, b in zip(times, times[1:])],
        "gui_gaps":   [b - a for a, b in zip(gui_stamps, gui_stamps[1:])],
        "dropped":    pipeline.dropped_count,
        "t0_wall":    pipeline.t0_wall,
    }


def test_every_frame_reaches_the_pipeline(replay):
    """Intake off the GUI thread must not cost frames — nothing is dropped."""
    assert len(replay["times"]) == N_FRAMES
    assert replay["dropped"] == 0


def test_the_gui_thread_really_did_fall_behind(replay):
    """Guard against a vacuous pass.

    If the GUI handler kept up, the test below would prove nothing — the two
    cadences would coincide. Assert the stall actually happened first.
    """
    gui_median = statistics.median(replay["gui_gaps"])
    assert gui_median >= GUI_STALL * 0.8, (
        f"GUI handler median gap {gui_median*1000:.0f} ms — the stall this test "
        "depends on did not materialise, so its result is meaningless"
    )


def test_timevec_follows_the_camera_not_the_stalled_gui(replay):
    """The core of task 5: sampling rate is set by capture, not by the GUI."""
    frame_interval = 1.0 / FPS
    cap_median     = statistics.median(replay["cap_gaps"])

    # Midpoint between the two cadences: anything below it is camera-paced,
    # anything above is GUI-paced. Before this change the timestamps came off
    # the GUI thread and landed near GUI_STALL.
    midpoint = (frame_interval + GUI_STALL) / 2.0
    assert cap_median < midpoint, (
        f"timeVec median gap {cap_median*1000:.0f} ms sits closer to the GUI "
        f"cadence ({GUI_STALL*1000:.0f} ms) than to the camera's "
        f"({frame_interval*1000:.0f} ms) — timestamps are being taken on the "
        "GUI thread again"
    )
    # Sanity floor: msleep granularity and per-frame TIFF reads add jitter on
    # top of the nominal interval, but a gap far *below* it would mean the
    # timestamps are not tracking real capture at all.
    assert cap_median >= frame_interval * 0.5, (
        f"timeVec median gap {cap_median*1000:.0f} ms is implausibly short for "
        f"a {FPS:.0f} Hz source"
    )


def test_wall_clock_anchor_is_latched_for_the_session(replay):
    """t0_wall gives the absolute `startTime` the results schema needs."""
    assert replay["t0_wall"] is not None
    assert replay["t0_wall"] > 0
