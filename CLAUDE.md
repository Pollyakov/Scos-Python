# CLAUDE.md

## Purpose

SCOS (Speckle Contrast Optical Spectroscopy) — real-time GUI app that acquires frames from a Basler camera, computes speckle contrast (κ²) with noise correction, and plots blood flow (1/κ²) over time. Translated from MATLAB (SCOSvsTime_WithNoiseSubtraction_Ver2.m). Used in the Optical Neuroimaging Lab at Bar-Ilan University.

## Setup & Run

```bash
# Windows setup
setup.bat
# Or manually:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run app
python main.py

# Verify camera
python check_camera.py

# Benchmark (no camera needed)
python bench_processor.py
python bench_processor.py --width 2448 --height 2048 --window 7 --duration 30 --bits 12 --fps 50
```

## Testing

```bash
python -m pytest tests/
```

A pre-commit hook runs all tests before each commit; failures block the commit.

## Architecture

```
Thread 1 (CameraThread/QThread):  pypylon grabs → emits frame_ready (every frame)
                                                 → emits display_ready (≤30 FPS)
Main thread (GUI):                frame_ready  → _on_scos_frame() → SCOSProcessor.process() → PlotWidget.append()
                                  display_ready → _on_display_frame() → ImageWidget.update_frame()
```

IMPORTANT: Processing runs on the GUI thread. If `process()` exceeds the frame budget (1000/FPS ms), the GUI lags and frames drop.

## Key Modules

- `camera.py` — CameraThread wraps pypylon. Supports Mono8/10/12, hardware trigger (Line2), live parameter changes
- `processor.py` — SCOSProcessor: local variance via `scipy.ndimage.uniform_filter`, noise corrections (shot, dark, quantization)
- `gui/main_window.py` — wires camera, processor, GUI controls, .mat/.npz export
- `gui/image_widget.py` — pyqtgraph ImageItem + circle ROI (auto-detect or manual drag)
- `gui/plot_widget.py` — real-time 1/κ² time-series plot (incremental append, no full redraw)

**Dependencies:** PyQt6 for GUI, pyqtgraph for fast image/plot rendering, pypylon for Basler cameras, numpy/scipy for computation, pyserial for Arduino communication, tifffile/h5py for file I/O.

## Critical Gotchas

IMPORTANT: Exposure in GUI = **milliseconds**. Camera API (pypylon) = **microseconds**. Conversion: `exposure_us = gui_value * 1000`. Getting this wrong silently produces bad data.

IMPORTANT: `convert_gain(gain_db, bit_depth, sat_capacity)` returns DU/e (digital units per electron). Default sat_capacity = 10500. This constant is camera-model-specific.

- ROI mask: boolean ndarray, same shape as frame, generated from circle (cx, cy, r)
- Save format matches MATLAB convention: .mat with keys `scosTime`, `scosData` (κ²), `frameRate`, `exposureTime`, `Gain`
- Trigger mode "On" = hardware trigger on Line2; "Off" = internal frame rate
- When changing pixel format or trigger mode, camera must stop and restart grabbing
- Default camera params: Mono12, 8ms exposure, 20 Hz frame rate, gain 8 dB

## Code Style

- Python 3.11+, type hints on public functions
- PyQt6 signals/slots for thread communication — never access GUI from camera thread
- NumPy vectorized operations preferred over Python loops for image processing
- Use `np.float64` for all intermediate SCOS calculations to avoid precision loss

## Compaction Instructions

When compacting, preserve: list of modified files, current task, any test results or error messages, and the threading model (which thread does what).
