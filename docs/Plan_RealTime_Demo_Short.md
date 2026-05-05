# SCOS Real-Time Demo Plan — Short Version

## Constraints

- 1 day to prepare + half a day for the show.
- Limited access to the real Basler camera and laser.
- No need to store data, no long-record handling, no disk-space logic.

## Big idea

Replace the Basler camera with a **fake camera that replays a TIFF file**.
Everything else in the GUI stays the same. When the real hardware becomes
available, swap the fake back out for the real one — nothing else changes.

## Phase 1 — TIFF mock camera (target: 1 day, no hardware)

**Goals:**
- Confirm the SCOS math runs end-to-end.
- Measure the real per-frame processing time on a normal laptop.
- Be able to keep developing and demoing without lab equipment.

**Ideas:**

1. **A new `MockCameraThread`** that looks identical to the real
   `CameraThread` from the outside — same signals, same methods. Inside,
   it loads a TIFF stack into memory and emits frames one by one at the
   configured FPS.

2. **One CLI flag** in `main.py`: `--mock-tiff <path>`. With the flag, the
   app uses the mock; without it, the app uses the real camera. No other
   changes to behavior.

3. **A small synthetic-TIFF generator** so we always have a stack to play
   back, even if no real recording is available. The data only needs to
   look speckle-ish — it's for pipeline testing, not biology.

4. **The processing-time indicator already exists** in the status bar. We
   just smooth it over a longer window and stop it from updating 50 times
   per second.

5. **Skip calibration in Phase 1.** The math still produces a usable κ²
   without dark/bright calibration. We get back to calibration when the
   laser is plugged in.

**Setter behavior in mock mode:**
- FPS spinbox **does** change playback speed.
- Exposure / gain / pixel format / trigger / ROI controls stay on screen
  but don't change the frames — the TIFF brightness is fixed. Worth
  mentioning in the demo intro if anyone asks.

**Verification idea:**
Generate a synthetic stack, run `pytest`, then launch with `--mock-tiff`
and watch: frames stream, plot grows, processing-time label settles well
under the inter-frame budget.

## Phase 2 — real camera (target: ½ day, on lab PC)

**Idea:** drop the flag and run `python main.py` like before. The mock
and the real paths are independent, so Phase 1 cannot have broken Phase 2.

**Sanity checks before the show:**
- Run `check_camera.py` first to confirm the Pylon SDK still works.
- Open the GUI, start video, look at FPS label and processing-time label.
- Start SCOS with the laser on, watch the plot move.

**Likely failure points** (already handled by existing code paths):
- Pixel-format mismatch → existing warning surfaces in the GUI.
- Trigger-mode hang → existing error handler catches it.
- Pylon install regression → `check_camera.py` flags it.

## What we are deliberately not doing

- The full multi-week refactor (`core/` package, 3 worker threads, 2 bounded
  queues, HDF5 recorder, overload dialog, full state machine, calibration
  phases) — those stay in `docs/Plan_RealTime.pdf` /
  `docs/Full_Plan_RealTime.pdf` / `docs/Plan_RealTime_Patches.md` for
  later, post-demo.
- Long-record handling, 4-hour cap, disk-space management.
- Storing measurement data to disk.
- Hardware-trigger Arduino integration in mock mode.

## Files at a glance

- New: `mock_camera.py`, `tools/synth_tiff.py`, `tests/test_mock_camera.py`.
- Edit: `main.py` (CLI flag), `gui/main_window.py` (accept camera as
  argument, throttle the proc-time label).

The full version with code skeletons and exact wiring is in
[`Plan_RealTime_Demo.md`](Plan_RealTime_Demo.md).
