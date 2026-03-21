# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SCOS (Speckle Contrast Optical Spectroscopy) — a real-time desktop application for acquiring camera frames from a Basler camera and computing speckle contrast (κ²) with noise correction. Translated from a MATLAB codebase (SCOSvsTime_WithNoiseSubtraction_Ver2.m).

## Setup & Run

```bash
# First-time setup (Windows)
setup.bat
# Or manually:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Run the application
python main.py

# Verify camera connection
python check_camera.py

# Benchmark processor (no camera needed)
python bench_processor.py
python bench_processor.py --width 2448 --height 2048 --window 7 --duration 30 --bits 12 --fps 50
```

## Architecture

**Threading model:** CameraThread (QThread) grabs every frame and emits two signals:
- `frame_ready` — every frame, connected to SCOS processing on the GUI thread
- `display_ready` — capped at 30 FPS, connected to image display

Processing happens synchronously on the GUI thread in `MainWindow._on_scos_frame()`. If processing exceeds the frame budget, the GUI will lag.

**Data flow:** Camera → CameraThread.run() → frame_ready signal → MainWindow._on_scos_frame() → SCOSProcessor.process() → PlotWidget.append()

**Key modules:**
- `camera.py` — CameraThread wraps pypylon (Basler Pylon SDK). Supports Mono8/10/12, ROI, hardware trigger (Line2), live parameter changes
- `processor.py` — SCOSProcessor computes κ² per frame using sliding-window local variance (scipy uniform_filter). Includes shot noise, dark noise, and quantization noise corrections
- `gui/main_window.py` — MainWindow ties everything together: controls panel, signal wiring, .mat/.npz export
- `gui/image_widget.py` — pyqtgraph ImageItem display with circle ROI (auto-detect or manual draw)
- `gui/plot_widget.py` — Real-time 1/κ² (rBFI) time-series using pyqtgraph

**Dependencies:** PyQt6 for GUI, pyqtgraph for fast image/plot rendering, pypylon for Basler cameras, numpy/scipy for computation, pyserial for Arduino communication, tifffile/h5py for file I/O.

## Key Details

- Exposure in the GUI is in **milliseconds**, converted to **microseconds** for the camera API (`* 1000`)
- The processor's `convert_gain()` converts dB gain to DU/e (digital units per electron) using saturation capacity
- ROI mask is a boolean ndarray the same shape as the frame; generated from a circle (cx, cy, r)
- Save format matches MATLAB convention: .mat files store `scosTime`, `scosData` (κ²), `frameRate`, `exposureTime`, `Gain`
- Camera trigger mode "On" uses hardware trigger on Line2; "Off" uses internal frame rate
