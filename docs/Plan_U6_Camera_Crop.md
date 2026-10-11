# Plan — U6: crop on the camera when "Cut Image" is clicked

Written 2026-10-11. Task: `docs/todo.md`, row **U6** (decided with Vika 2026-10-08).
Status: **plan only — nothing implemented yet.** Decisions in section 7: all decided (2026-10-11) — ready to implement.

---

## 1. Goal

When the operator clicks **"Cut Image"**, the **camera itself** sends only a small
rectangle around the ROI circle — for the live display *and* for SCOS processing.
The dark and bright calibrations are recorded **after** that, so they have exactly the
same size and position as the measurement frames. **"Full Image"** goes back to the
whole sensor. From Start SCOS until the measurement ends, the crop cannot change.

Why: today every frame is the full 1216 × 1936 sensor (4.7 MB). A crop around a
typical ROI is a few hundred pixels square — about 10–30× less data per frame →
faster processing, faster calibration collectors (helps U11), smaller
`Calibration.h5` (24 MB today), fewer lost frames.

---

## 2. How it works today (verified in the code)

| Piece | What it does now |
|---|---|
| "Cut Image" button (`gui/image_widget.py:203–230`, `_apply_cut`) | **Display only.** Cuts a square around the circle (radius + 20 px margin) out of the full frame and shows that. The camera keeps sending the full sensor; processing uses the full frame. |
| `CameraThread.set_roi(x, y, w, h)` (`camera.py:157`) | Exists, **never called**. Stores the rectangle, and if grabbing: stops, `_apply_params()` writes `OffsetX/Y`, `Width/Height`, restarts. No rounding to the camera's allowed steps. |
| `_apply_params()` (`camera.py:248–262`) | With no ROI it resets to the full sensor — so a crop left in the camera from a previous run is undone at every Start Video. Good, keep. |
| Frame-size change (`main_window.py:2162`) | If a frame arrives whose shape differs from `_mask`, the mask is **reset to "whole frame"** and the processor told so. With a camera crop this would **silently throw the ROI away** (see pitfall P1). |
| ROI circle and mask (`image_widget.py:_make_mask`) | Built in the coordinates of the frame being shown. |
| Processor (`processor.py:354`, `set_roi`) | Cuts a box around the mask with a margin of `window//2 + 1` px and processes only that. |
| ROI lock (`main_window.py:705`) | ROI buttons/circle locked only in `MEASURING_INIT` / `MEASURING` — **not during the calibrations**, and "Cut Image" is never locked. |
| Playback sources (`folder_camera.py:140`, `mock_camera.py:99`) | `set_roi()` only stores the values; frames are never cropped. |

---

## 3. The accuracy argument — and how we prove it

The processor already works only on a box around the ROI (margin `window//2 + 1`).
Every filter it uses (`uniform_filter` for local variance, the dark-variance filter, the
bright `spVar` filter) reads only pixels within `window//2` of each ROI pixel. So **if
the camera crop keeps at least that margin around the circle, every number the
measurement uses is computed from exactly the same pixels.**

**Same pixels, but not bit-for-bit the same numbers** — measured 2026-10-11 on a random
1216 × 1936 array, comparing `uniform_filter` on the full array with the same filter on a
420 × 420 crop of it, over the crop's interior:

| data type | window 7 | window 51 |
|---|---|---|
| float64 (the calibration arrays `dark_var`, `spVar`) | differ, max relative 8 × 10⁻¹⁵ | differ, max relative 7 × 10⁻¹⁵ |
| float32 (κ² in `process()`) | identical | identical |

The filter keeps a running sum along each line, so where the line starts (sensor edge vs
crop edge) changes the last few binary digits. That is rounding noise, about 10¹¹ times
smaller than the 0.45–1.2 % agreement with MATLAB. And note: `process()` computes κ² in
**float32**, not float64 (todo A4 — a known deviation from the CLAUDE.md rule). So the
promise is: **κ² with the camera crop equals κ² without it to float32 rounding**, and the
calibration arrays agree to ~10⁻¹⁴ relative.

**The margin must cover the largest window.** The Window Size field goes up to **51**,
which needs **26 px**; "Cut Image" today adds only **20 px**. The plan uses a margin of
**max(20, 51 // 2 + 1) = 26 px**, plus whatever the camera's step rounding adds, so no
window size can ever reach past the crop edge. (A circle touching the sensor edge is
clamped to the sensor exactly as today — same pixels either way.)

**Proof test (new, offline, real data):** take the lab recording's frames and dark
frames, run calibration + κ² on the full frames, then crop every frame with the crop
function and run it again, for window 7 and the maximum 51. Tolerances, with the reason
written into the test: calibration arrays (float64) `rtol = 1e-12`; κ²_raw and κ²_corr
(float32) `rtol = 1e-6`. The test also prints the largest difference it actually saw, so
the margin is visible. A bigger difference means wrong pixels — a bug, not rounding. The
existing slow MATLAB tests (`test_dark_cal_offline.py`, `test_bright_cal_offline.py`)
must still pass.

---

## 4. Design

### 4.1 One crop, owned by MainWindow
A single record of the crop in use:
`{offset_x, offset_y, width, height, sensor_width, sensor_height}` (`None` = full
sensor). It is **read back from the camera after applying**, never taken from what we
asked for — the camera may round. U10 will write exactly this record into the results
file and `Calibration.h5`.

### 4.2 Crop arithmetic in `core/` (no math in the GUI)
New pure functions in a small `core/crop.py`:
- `crop_for_circle(cx, cy, r, margin, sensor_w, sensor_h, steps) -> (x, y, w, h)` —
  square around the circle + margin, clamped to the sensor, **offset rounded down and
  size rounded up** to the camera's steps (`OffsetX.Inc`, `Width.Inc`, `Width.Min`, …),
  and checked to still contain circle + margin.
- `circle_mask(frame_shape, circ, offset) -> mask` — the ROI mask for a frame that
  starts at `offset` on the sensor.

### 4.3 The ROI circle stays in **sensor coordinates**
The circle the operator sees and moves is always in full-sensor pixel coordinates. A
cropped frame is drawn at its offset (`setPos(offset_x, offset_y)` — the display cut
already does this), so the circle does not jump when the camera crops. The mask for a
cropped frame is the circle shifted by the offset (`circle_mask`). Benefits: the
`Mask.mat` circle in playback stays valid; `Params.ROI` keeps meaning the same thing
(see decision D3).

### 4.4 Camera side (`camera.py`)
- `set_roi()` becomes: read the camera's steps and limits, round; **if** a
  sensor-centering *switch* exists and is on, turn it off — `CenterX/Y`, which Basler
  documents for the older ace Classic/U/L cameras ("set the CenterX and CenterY
  parameters to false"); while on, `OffsetX` cannot be written. *Corrected while
  implementing:* `BslCenterX/Y` (newer cameras, present on the emulator) is a one-time
  **command** that centres the area, not a mode — there is nothing to turn off, and it
  is left alone. Which nodes the a2A1920 has is confirmed on the rig camera. Then apply,
  **read back** the applied values, log them
  ("Camera crop: 412 × 412 at (760, 402) of 1936 × 1216").
- A new method (or `set_roi(None)`) for "Full Image".
- Same stop → apply → restart pattern as `set_trigger()` / `set_pixel_format()`, which
  already worked on the rig (trigger restarts, four of them on 2026-10-08, camera clock
  re-accepted each time). `_restart_clock()` already handles the frame-number gap.
- **Never hard-code the steps.** The emulator reports step 1 for everything and accepts
  `Width = 101`; the real a2A1920 very likely has larger steps (unconfirmed) — read
  `Inc`/`Min`/`Max` from the camera at run time.

### 4.5 Frame-size changes (fixes pitfall P1)
The frame size changes **only** when the operator clicks Cut/Full Image, and that is
possible **only before a run** — the lock (4.6) blocks it from Start SCOS on (user's
decision 2026-10-11: rely on the lock; no extra mid-run size check for possible future
bugs or features).
- On Cut/Full: the mask is rebuilt **at once** from the circle and the new crop
  (`circle_mask`).
- A frame whose size does not match the current crop — only the few old-size frames
  still queued in Qt right after the click — is **skipped** (one line in `app.log`), not
  used to reset the mask. This replaces "shape changed → mask = whole frame"
  (`main_window.py:2162`), so the ROI is never lost. Losing a few preview frames is
  harmless, and the skip also keeps any such frame away from the dark collector if Start
  SCOS is clicked immediately after Cut.
- "No ROI drawn yet → whole frame" stays as it is.

### 4.6 Lock
"Cut Image"/"Full Image" disabled from **Start SCOS** (dark calibration) until the run
has fully ended — **after the laser-off check**. That check takes a fresh frame and
measures it with the measurement's mask (`main_window.py:2049`), so the crop must not
change before it. Careful: Stop SCOS re-enables the parameter fields
(`_set_params_enabled(True)`) *before* the laser-off check runs, so the Cut lock must
**not** be released together with them — release it after `_finish_session()` (or on
the state leaving FINISHED). See decision D2 for the ROI circle itself.

### 4.7 Playback (`--mock-folder`) and mock sources
`FolderMockCamera.set_roi()` will really crop: it slices each frame (main *and* dark
folder) before emitting. Then the rehearsal can test the whole crop path, including
calibration on cropped frames. `--mock-tiff`'s `MockCameraThread` gets the same slice.
`--mock-h5` replays results only — unaffected.

### 4.8 Small things
- Info panel: "Size: 412×412 (crop of 1936×1216)".
- The crop is **not** saved between launches (the ROI is not either); every launch
  starts on the full sensor — `_apply_params()` already guarantees that.
- "Cut Image" needs a running video and an ROI circle; otherwise it stays disabled.

---

## 5. Pitfalls found while planning

- **P1 — the ROI would be silently dropped.** `main_window.py:2162` resets the mask to
  "whole frame" whenever the frame size changes. Without 4.5, clicking Cut Image would
  make the next measurement use the whole crop, not the circle — no error, wrong data.
  Fixed by 4.5 (rebuild on Cut/Full, skip wrong-size frames); a size change *during* a
  run is prevented by the lock (4.6), with no separate check (user's decision).
- **P2 — margin too small for large windows.** 20 px < 26 px needed for window 51
  (section 3).
- **P3 — changing the crop during calibration** would mix two frame sizes in the dark
  collector (error) or pair a dark calibration of one crop with a measurement of another
  (wrong data). Hence the lock from Start SCOS (4.6).
- **P4 — camera step rounding.** If the camera rounds our rectangle, a mask built from
  the *requested* rectangle would be shifted by a few pixels. Hence "read back" (4.1).
- **P5 — sensor centering.** With the older cameras' `CenterX/Y` switch on, offsets
  can't be written (4.4). (`BslCenterX/Y` is a command, not a switch — see 4.4.)
- **P6 — playback auto-calibration** (`_auto_load_folder_calibration`) loads full-size
  arrays from the recording folder. They are replaced by the live calibration at Start
  SCOS, so no measurement uses them — but a check that nothing reads them with a cropped
  mask before then is part of the work.

---

## 6. Steps (in order)

| # | Step | Test / check |
|---|---|---|
| 1 | `core/crop.py`: `crop_for_circle`, `circle_mask` | Unit tests: rounding to steps of 1/2/4/16, clamping at every sensor edge, circle + 26 px always inside, odd sizes |
| 2 | **Accuracy proof** (section 3) on the lab recording, windows 7 and 51 | κ² with and without crop equal to float32 rounding, calibration arrays to 1e-12; slow MATLAB tests still pass. **✅ Done 2026-10-11** — `tests/test_crop_accuracy_offline.py` (slow): κ²_raw, κ²_corr, ⟨I⟩ and spVar came out **exactly identical**, dark_var within 9 × 10⁻¹⁵ relative, for the Mask.mat circle (crop cut by the sensor edges) and a radius-150 circle (crop 360 × 354 inside the sensor). With the old 20 px margin, window 51 fails: 132 ROI pixels with dark_var off by up to 0.9 %. Slow suite 6/6 |
| 3 | `camera.py`: rounding, centering off, read-back, full-sensor reset, logging | Fake-Pylon tests in `tests/test_camera.py`; then the **pylon emulator** (`PYLON_CAMEMU`) — real stop/apply/restart and frame shape. **✅ Done 2026-10-11** — `set_roi()` / new `clear_roi()` return the read-back `applied_crop`; `camera_steps()`, `sensor_size()`; a refused crop falls back to the full sensor with a warning (camera never left stopped); log line "Camera image area: W × H at (x, y) of the … sensor". 9 new fake-camera tests (the fake enforces steps and OffsetX + Width ≤ sensor); 4 deliberate mistakes each caught. Emulator: crop 354 × 354 applied while grabbing, every following frame 354 × 354, Full Image back to 4096 × 4096 |
| 4 | Mock sources really crop (`folder_camera.py`, `mock_camera.py`) | Frame shape = crop, main and dark folders. **✅ Done 2026-10-11** — shared `core.crop.PlaybackCrop` (one-tuple swap, contiguous copy of the crop, refuses an area outside the frames); both sources gained `set_roi`→Crop, `clear_roi`, `camera_steps` (steps 1), `sensor_size`, `applied_crop`. 7 new tests (recording and dark folder cut, Full Image, refusal); 5 deliberate mistakes each caught. Found on the way: a test-order crash (exit 127) — fixed by creating the `QApplication` in `tests/conftest.py` |
| 5 | `image_widget.py` + `main_window.py`: Cut/Full call the camera; circle in sensor coordinates; mask rebuilt on Cut/Full and wrong-size frames skipped (P1); lock from Start SCOS until after the laser-off check; Info label | Window tests: cut → mask matches the circle in the crop; an old-size frame after the cut is skipped and does not reset the ROI; buttons disabled from DARK_CAL until after the laser-off check (still disabled while it runs); Full Image restores |
| 6 | Rehearsal: new `--scenario cut` (click Cut before Start SCOS) | Frames, calibration arrays and mask in `Calibration.h5` are crop-sized; κ²_corr like `normal`; all existing checks pass; `normal`, `slowdown`, `recovery` still pass |
| 7 | Docs: todo U6 → done + Done item, CLAUDE.md (frame-size line, rig camera row), `rig_checklist.md` (new rig check, below), `simulation_checklist.md` | — |

**Rig check to add (needs the real camera):** with the ROI on the subject, click Cut
Image → `app.log` shows the applied crop and the camera's real steps; the image shows
only the crop; FPS stays at 20; "Camera clock accepted" appears again after the
restart; run a short session → κ²_corr in the same range as an uncut session;
`Calibration.h5` much smaller than 24 MB. Then Full Image → full sensor again.

**Estimate:** ~5–6 h (steps 1–2 ≈ 1.5 h, 3–4 ≈ 1.5 h, 5 ≈ 1.5 h, 6–7 ≈ 1 h), plus the
rig check.

---

## 7. Decisions needed before starting

| # | Question | Recommendation |
|---|---|---|
| D1 | While the camera is cropped, can the operator move/resize the ROI circle? | ✅ *Decided 2026-10-11 (user): as recommended.* **No — locked while cut; click "Full Image" to change the ROI, then "Cut Image" again.** The alternative (crop follows the circle) restarts the camera on every drag, and the circle could be dragged off the part of the image that is still visible. |
| D2 | Lock the ROI circle (not just Cut/Full) from **Start SCOS**, i.e. also during the two calibrations? Today it is locked only once the measurement starts. | ✅ *Decided 2026-10-11 (user): as recommended.* **Yes.** The protocol has the ROI picked before Start SCOS, and the mask is written into `Calibration.h5` at the end of the dark calibration — moving it later makes that record wrong. |
| D3 | `Params.ROI` (cx, cy, r) in the results file: sensor coordinates or crop coordinates? | ✅ *Decided 2026-10-11 (user): option A.* **Sensor coordinates** (as today), with the crop offsets added by U10 — then the circle means the same thing in every file, cut or not. Worth one line to Vika with U10. |
| D4 | Make playback (`--mock-folder`) really crop, so the rehearsal tests the full path? | ✅ *Decided 2026-10-11 (user): as recommended.* **Yes** (step 4). |

---

## 8. Not in U6
- **U10** — writing the crop into `metadata` / `Calibration.h5` (next task; the record
  in 4.1 is shaped for it).
- **U11** — dark calibration at the maximum frame rate (needs D8).
- Moving the calibration collectors off the GUI thread.
