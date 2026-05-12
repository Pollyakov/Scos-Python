# SCOS — Implementation Backlog (from Full_Plan_RealTime.pdf)

Last updated: 2026-05-12

---

## ✅ Done

| # | Task | Notes |
|---|------|-------|
| 1 | Auto-save to HDF5 wired into GUI | Folder picker on Start SCOS, status bar shows path |
| 2 | `HDF5Recorder` class (`core/recorder.py`) | Buffered writes, flushes every 300 points |
| 3 | `closeEvent` flush | `recorder.close()` called before window exit |
| 4 | Metadata saved with session | fps, gain, exposure, window, ROI, sat_capacity → HDF5 `metadata` group |

---

## ⚠️ Partial

### 5 · rBFI real-time normalization
The plot axis is already labeled `1/κ² (rBFI)` and `SessionConfig` has a `norm_seconds`
field, but the actual normalization is never computed. Currently the app plots raw `1/κ²`.
**To complete:** compute the mean of the first N seconds of BFI and divide all subsequent
values by it.

### 6 · Full state machine
`core/session.py` defines the full `State` enum (IDLE, DARK_CAL, BRIGHT_CAL,
MEASURING_INIT, MEASURING, FINISHED, ERROR) but it is never imported or used.
The GUI instead uses boolean flags (`_scos_active`, `_dark_cal_collector`) to track
what is happening — there are no automatic state transitions.

---

## ❌ Not started

### 7 · `core/pipeline.py`
The PDF calls for a `RealtimePipeline` class with a frame queue of size 20 connecting
the camera thread to the processing thread. Currently the worker uses a 1-frame drop
queue (`queue.Queue(maxsize=1)` in `_SCOSWorkerThread`). The full pipeline with
backpressure handling does not exist yet.

### 8 · `core/frame_source.py` ABC
The PDF wants a clean `FrameSource` abstract base class so that `CameraThread`,
`FolderMockCamera`, and any future source are interchangeable. Currently each camera
is a separate class with no shared interface.

### 9 · `core/scos_math.py`
The PDF wants a pure-math module with no camera/GUI imports — just functions like
`compute_kappa2()`, `local_mean_and_variance()`. Currently all math is inside
`processor.py` mixed with calibration state. This refactor would make unit testing
much easier.

### 10 · Queue fill indicator + dropped-frame counter in GUI
No visual indicator shows how full the frame queue is. The PDF wants a small bar or
percentage label so the user can see if the system is falling behind before frames
start dropping.

### 11 · Overload high-water-mark dialog
No high-water-mark logic. The PDF says when the queue is 80% full, pause capture and
show a dialog with 4 options: reduce ROI, reduce FPS, save-only mode, or
process-only mode.

### 12 · `GrabStrategy_OneByOne` in `camera.py`
`camera.py` currently uses `GrabStrategy_LatestImageOnly`, which silently discards
frames when the system is slow — dangerous for SCOS because FFT of the BFI signal
assumes uniform sampling. `OneByOne` keeps all frames in a buffer (up to
`MaxNumBuffer=20`) and lets the code detect losses via `GetNumberOfSkippedImages()`.

### 13 · `shrink_mask_for_window`
Per protocol: the ROI mask should be eroded by `window//2 + 1` pixels before computing
κ², because the box filter near ROI edges uses pixels outside the ROI and corrupts the
result. Currently the full ROI circle is used as-is.

### 14 · `setDownsampling` in plot
We added percentile-based Y-axis scaling to handle spikes, but for long recordings
(100k+ points) pyqtgraph will lag without `setDownsampling(auto=True, mode='peak')`
+ `setClipToView(True)`. These are one-line additions to `gui/plot_widget.py`.

### 15 · Disk-space check
Before starting a recording, `shutil.disk_usage()` should verify there is enough free
space. During a long session it should also periodically check and stop if space drops
below a threshold (e.g. 1 GB).

### 16 · `logging` module
All diagnostic output currently goes via print statements or Qt status bar messages.
Replacing with Python's `logging` module would write a session log file that can be
reviewed after a crash.

### 17 · `pyproject.toml` + `requirements.lock`
Only `requirements.txt` exists. `pyproject.toml` would consolidate all tool
configuration (pytest, ruff, mypy) in one place and enable `pip install -e ".[dev]"`.

### 18 · GitHub Actions CI
No `.github/workflows/` directory. CI would run ruff + pytest automatically on every
push.

### 19 · `docs/realtime_architecture.md`
A document answering the architecture questions from the PDF (which threads, which
queues, state machine transitions, data formats, open questions for the supervisor).
Not written yet.
