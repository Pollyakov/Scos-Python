# Plan — saving the raw frames

Written 2026-10-20. Task: `docs/todo.md`, "Save raw frames option" (F1), together with the
free-disk-space check (B2) and writing on a background thread (F4).

**Why:** Vika wants to rerun her MATLAB script on frames this app saved and compare with the
app's results. The version-0 tag waits for that check.

**Already decided by Vika (2026-10-07):** every frame; one file per frame; a `Frames` folder
inside the session folder; the format chosen by measurement.

---

## 1. What was measured (2026-10-20, this PC = the rig PC)

**Disk:** NVMe SSD (Crucial CT1000E100), 170 GB free.

| Format | Full frame 1936 × 1216 | Cut 672 × 667 | Readable by |
|---|---|---|---|
| **TIFF, uncompressed** | 2.7 ms, 4.71 MB | 2.3 ms, 0.90 MB | MATLAB script, app playback, Pylon Viewer |
| TIFF, zlib compression | 19.5 ms (worst 51 ms), 2.10 MB | 11.9 ms, 0.39 MB | same |
| `.npy` | 3.9 ms, 4.71 MB | (noisy) | Python only |

Sustained (15 GB written without a pause): **≈ 960 MB/s = ≈ 200 full frames per second.**
The camera sends at most 76 full frames per second (dark calibration at maximum rate,
360 MB/s) and 20 per second in a measurement (94 MB/s). **The disk keeps up with everything.**

**Choice: uncompressed TIFF.** It is fast, it is what Pylon Viewer writes, and both the MATLAB
script and the app's `--mock-folder` playback read it unchanged. Compression saves only half
the space and is 7× slower.

**Space:** full frame at 20 Hz = 340 GB per hour, so 170 GB free ≈ **30 min**. After Cut Image
(672 × 667) = 65 GB per hour ≈ **2.5 h**.

## 2. What her MATLAB script needs (read from its code)

- A folder of `*.tiff` files (exactly `.tiff` — it does not look for `.tif`).
- The camera serial number in the file name, between `__`:
  `Basler_a2A1920-160umBAS__40075248__<index>.tiff` — the same pattern the playback reads (Pylon also puts its recording time before the index; left out, see step 4).
- 16-bit TIFF. It then assumes Mono12 (`GetRecordInfo.m`), which is what the rig uses. Mono10
  would be read wrongly — so frame saving is for Mono12 (and Mono8) only; the app says so if
  another format is set.
- The settings in the **folder name**: `expT8ms_Gain8dB_FR20Hz_BL100DU`. BL (the camera's
  black level) is subtracted from both the frames and the dark mean, so it cancels — the name
  must carry the real value (100 on the rig camera).
- The dark frames in a folder next to it, with `_dark` added to the name; at least 400 frames.
- She takes `spVar` from the first 600 frames of the recording itself, not from separate
  bright frames. So her κ² will differ a little from the app's (different frames for the
  same correction). The comparison will show how much.

## 3. Folder layout

```
<session folder>/
  rBFi_resultsAndParameters.h5, Calibration.h5, rBfi_fig.png   (as today)
  Frames/
    expT8ms_Gain8dB_FR20Hz_BL100DU/         measurement frames
    expT8ms_Gain8dB_FR20Hz_BL100DU_dark/    dark-calibration frames
    expT8ms_Gain8dB_FR20Hz_BL100DU_bright/  bright-calibration frames
    frame_times.csv                          file name, phase, capture time, frame number
```

**Exactly the frames the math used:** dark and bright = the frames the calibration took (not
the ones it skipped); measurement = from the first frame of `timeVec` on. `frame_times.csv`
ties every file to its time, so the files can be matched to `timeVec` one to one.

## 4. How it works

- A **frame writer** with its own thread and a short waiting line (about 2 s of frames).
  The camera thread and the window only hand it a frame — they never wait for the disk.
- If the line is full, that frame is **not saved and is counted**; the measurement goes on.
  The count goes into the results file (`metadata`) and the status bar.
- **Disk-space check** before Start SCOS, from the frame size, frame rate, the set duration and
  the calibration frame counts; and every 30 s while recording.
- The old code (`append_frame`, one growing dataset inside the results file) is removed.

## 5. Decisions — all four taken as recommended (user, 2026-10-20: "go ahead")

| # | Question | Recommendation |
|---|---|---|
| 1 | Save which phases? | **All three** — dark, bright, measurement. Her script needs the dark frames; the bright ones let us rerun the app's own calibration. ≈ 5.6 GB extra for 600 + 600 full frames |
| 2 | If saving can't keep up? | **Skip that frame, count it, warn** — the measurement is never disturbed. (On this PC it should not happen) |
| 3 | Free-space limits? | **Start only if free space ≥ estimate × 1.2 + 5 GB; stop the session cleanly below 2 GB.** Proposed to Vika on 2026-10-07, never confirmed — kept as named constants, easy to change |
| 4 | "Unlimited" duration with frames on? | **Estimate for 4 h** (the protocol's maximum) — so on the full frame it will refuse to start. The operator then sets a duration or cuts the image |

## 6. Steps (I stop and report after each)

| # | Step | Est. |
|---|---|---|
| 1 | Frame writer (`core/frame_writer.py`): thread, waiting line, skip count, TIFF names, `frame_times.csv`, clean close. Tests | 1 h — ✅ 2026-10-21: `tests/test_frame_writer.py` (19); six planted mistakes each caught (index without fixed width, no file time, a skip leaving a hole in the numbers, a waiting `put()`, closing that hangs with a full queue, a write error escaping); MATLAB's `GetRecordInfo` read a written folder: Mono12, SN, expT/Gain/FR/BL, `_dark` found, frames in order. Fast suite 726 passed |
| 2 | Disk-space estimate and check (`core/`). Tests | 45 min — ✅ `core/disk_space.py`, 11 tests |
| 3 | Connect it in the app: the three phases, folder names, metadata, the checkbox on again (only Mono12/Mono8), status bar. Update `tests/test_save_frames_disabled.py` | 1.5 h — ✅ pipeline `frame_sink` (measurement frames on the camera thread), `MainWindow`; the old test replaced by `tests/test_save_frames.py` (21; ten planted mistakes caught). Mono10 is warned about in `app.log`, not refused |
| 4 | Rehearsal with frames on: file counts = calibration frames and `timeVec`; then replay the saved session with `--mock-folder` | 45 min — ✅ `tools/rehearsal.py --save-frames` (600 + 600, cut, 40 s: all checks pass). **Found:** the replay ran with default settings and a fixed ×64 scale — playback now reads expT/Gain/FR/BL from the folder name and the scale from the frames. **Found:** Windows' 260-character path limit cost a session every bright frame — names shortened, too-long paths refused |
| 5 | **MATLAB check here:** run her script on a saved session, compare κ² with the app's results file | 45 min — ✅ (below) |
| 6 | Docs (CLAUDE.md, rig checklist, todo) | 30 min — ✅ CLAUDE.md note, `rig_checklist.md` R8, `expectations.md` §8, todo Done item 58 |
| 7 | On the rig: one short session with frames on (cut and full) | on the rig — ✅ cut, 2026-10-22 (`rig_checklist.md` R8, session `scos_20261022_165057`: 600 + 600 + 1301 saved, 0 skipped; rerun on the files = results file exactly). Full frame not run on the rig |

Total ≈ 5.5 h of work before the rig.

## 7. MATLAB check on the dev PC (2026-10-21, step 5)

Session: `tools/rehearsal.py --save-frames --cut --cut-radius 300 --measure-seconds 40`
(lab recording, 654 × 654, 600 + 600 calibration frames, 1250 measurement frames). Her script
`SCOSvsTime_WithNoiseSubtraction_Ver2.m` with baseFunc from `main` (`vika_tmp` lacks `LoadG.m`
and `ReadRecordVarAndMean.m`), on a copy of the saved folders with the values divided by 4:
the lab frames are 10-bit stored ×64, and her script reads a 16-bit TIFF as Mono12 (it then
divides by 16). ROI = `Calibration.h5` `dark/mask`.

| Check | Result |
|---|---|
| Her script finds the dark folder, reads SN, expT, Gain, FR, BL | yes — no question asked, no error |
| Frames matched by name (`tools/compare_with_matlab.py`) | 1250 / 1250, the same order |
| raw κ², per frame | 0.05 % apart on average, 0.19 % at most |
| ⟨I⟩, per frame | 0.09 % apart on average |
| The app's own math rerun on the saved files vs the results file | **bit-identical** |
| Corrected κ² with her G and her spVar in the app's math | 0.10 % apart on average, 0.27 % at most |

The corrected-κ² line is a weak test on this recording: read as 12-bit, the SN's G is
computed for Mono12 at 24 dB (5.88 instead of 1.46), and corrected κ² is negative in both.
With the rig's 12-bit frames G comes from the same table row on both sides. The first try
used `BL0DU` (playback reported no black level): her κ²_raw came out ≈ 2800 — `FitMeanIm`
fits the mean image to frames minus BL only. Fixed by taking BL from the recording's name.
