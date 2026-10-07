# Simulation checklist — a `--mock-folder` session, step by step

Rig-session prep step 2 (`docs/todo.md`). Written 2026-10-04 against commit `10c74ce`.

This is what you do, and what you should see, when you rehearse a whole SCOS session on
the lab recording instead of the real camera. It is the script for **step 3f** (your
hands-on GUI pass) — the only test that uses the real pop-up dialogs, which the automated
tests replace with stubs.

How to read each step: **Do** → **Expect** → **If not**. Tick the boxes as you go.
Anything that does not match — even if it looks harmless — goes into step 4.

> **The numbers below are real.** They come from headless runs of this exact sequence
> (normalization 3 s, 10 s of measurement): on 2026-10-04 with Dark/Bright Frames 60, and on
> 2026-10-05 with both 60 and 600 using `tools/rehearsal.py`. Every run completed, every file
> was written, and corrected κ² was positive at every point (304/304 with 60 frames, 305/305
> with 600). **Some values depend on the frame count**, so those are given for both 60 and
> 600. Your values will differ a little, because the recording loops and the bright frames
> land wherever the playback happens to be.
>
> **What that run covered, and what it didn't.** It confirmed A2–A3, B1–B8, B10 and
> section C (except the "over 2 min → mean" half of C5). Sections D, E, F and G, step B9,
> and the known issues K1–K3 are written from reading the code, not from a run. If one of
> those doesn't match, the checklist may be wrong rather than the app — note it either way.

---

## Before you start

- **Recording:** `C:\Users\USER\Scos_Frames_and_Results\expT5ms_Gain24dB_BL100DU_FR40Hz_005`
  (600 frames, 1216 × 1936, Mono10, 5 ms, 24 dB, 40 Hz; dark frames in the `_dark` folder
  next to it).
- **Launch:** type `/test-run` in Claude Code, or run
  ```
  venv\Scripts\python.exe main.py --mock-folder "C:/Users/USER/Scos_Frames_and_Results/expT5ms_Gain24dB_BL100DU_FR40Hz_005"
  ```
- **Results folder:** make an empty folder somewhere easy to find (e.g. `Desktop\scos_rehearsal`).
- **Dark/Bright Frames = 600** — the protocol default (`SCOS_protocol.md:20`), and the
  committed default since 2026-10-04 (it was 60, a testing shortcut).
- **How long each calibration takes depends on the frame count and on how fast this PC
  is.** The shortest possible is frames ÷ 40 Hz, when the PC keeps up with the playback;
  on a slower PC it takes several times that, because every frame is processed on the GUI
  thread. The **dark** calibration is the slower of the two — it computes each pixel's mean
  *and* variance, the bright one only the mean (todo D8).

  | Frames | Range | Measured on the dev PC, 2026-10-05 |
  |---|---|---|
  | 60  | ≈ 2–6 s each   | dark ≈ 4–5 s, bright ≈ 3–4 s |
  | 600 | ≈ 15–70 s each | dark ≈ 69 s, bright ≈ 41 s |

  While it runs, the "Dark cal: n / 600" counter may climb slowly (≈ 9 per second for
  dark on the dev PC) — that is the PC, not a hang. On the rig (700 × 700 at 20 Hz)
  600 frames should take about 30 s each.

### Three things that look wrong but are normal

1. **The status bar at the bottom keeps flickering "Frame #… shape=… min=… max=…".** It
   overwrites every other message there — see known issue K1 below.
2. **During the measurement the live image updates only every 2.5 s.** On purpose, to save
   GUI time. The FPS, κ² and ⟨I⟩ labels keep moving.
3. **Corrected κ² is lower than MATLAB's** (**0.0105** for this recording), and **how much
   lower depends on the frame count**:

   | Bright Frames | corrected κ² | 1/κ² | `spVar` (mean in ROI) |
   |---|---|---|---|
   | 60  | ≈ 0.0070–0.0076 (three runs) | ≈ 130–145 | ≈ 1.2 |
   | 600 | ≈ 0.0085 (one run)           | ≈ 115–120 | ≈ 0.5 |

   Two causes, neither a bug. **The dataset:** the recording was made with a subject in
   place, so the bright calibration taken from it contains the subject's speckle and its
   `spVar` comes out too large (about 2.3×); a larger `spVar` subtracted from the numerator
   gives a smaller κ². **The frame count:** `spVar` is measured on the *average* of the
   bright frames, and the random noise left in an average of N frames shrinks only as 1/N —
   with 60 frames enough is left to be counted as part of `spVar`, so κ² comes out lower than
   with 600. That is why the protocol asks for 600, and why raw κ² (≈ 0.093) is the same
   either way.
   On the rig the bright calibration is taken with the subject removed. The accuracy check
   against MATLAB lives in the offline tests (`tests/test_dark_cal_offline.py`,
   `tests/test_bright_cal_offline.py`), not here.

---

## A · Launch and preview

| | Do | Expect | If not |
|---|---|---|---|
| ☐ A1 | Launch the app | The window opens; status bar "Ready — camera not started" | Look at the terminal for a traceback |
| ☐ A2 | Click **Start Video** | The image appears. Camera box changes to **Mono10, 5 ms, 24 dB, 40 Hz** (read from the recording). **External Trigger is unticked and greyed out.** | Wrong values → the recording's `LocalStd7x7_corr.mat` was not read; check `app.log` |
| ☐ A3 | Wait | The Start SCOS button says **"Waiting for calibration…"** and the label says "Calibrating…" for **about 35–40 s** (it streams the dark folder in the background). Then the button reads **Start SCOS** and the label **"Cal OK — …"** | "Cal FAILED: …" → copy the message into step 4 |
| ☐ A4 | Look at the image | A **ROI circle** is drawn (it comes from `Mask.mat`) | No circle → `app.log`, search "Could not apply the ROI" |
| ☐ A5 | Look at the Info labels | **⟨I⟩ ≈ 120 DU**, FPS somewhere up to 40 | ⟨I⟩ in the thousands → the ×64 TIFF scaling is not applied (`_to_du`) |
| ☐ A6 | Check **Save Frames** | Greyed out, can't be ticked (by design since `10c74ce`) | |

---

## B · The main run

Set up first: type a **Recording name** (try one with a space and a colon, e.g.
`test run: 1`), leave Measuring duration at **∞**, Norm. type **Number of seconds, 5 s**.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ B1 | Click **Start SCOS** | **First pop-up: "Estimated G[DU/e]"** — "CameraSN 40513592 Mono10 is in the G[DU/e] table, but not at 24 dB … G was rescaled from the closest measured gain (G=1.4597 DU/e)". Normal: the table has this camera at 16/18/20 dB only. Click OK. | "Can't calculate SCOS … was not found" → the serial was not read from the recording; step 4 |
| ☐ B2 | — | **Second: the folder dialog** "Choose folder to save this session's results". Pick your empty folder. | |
| ☐ B3 | — | **Third: "Calibration — Step 1 of 2: Dark Frames"** — "Please turn off the laser." Click **OK**. This order (G → folder → laser) is the protocol's, `SCOS_protocol.md:11-17`. | Any other order → step 4 (it is asserted by `tests/test_recording_name.py`) |
| ☐ B4 | Watch | The parameter boxes turn dark grey (locked). The live image goes **darker** — playback switches to the dark folder — and **⟨I⟩ drops to ≈ 99 DU**. The label counts **"Dark cal: n / 600"** — slowly on this PC (see "Before you start"). You may briefly see **"Discarding N buffered frames…"** — normal, those were captured before you clicked OK. | ⟨I⟩ stays ≈ 121 during dark cal → playback did not switch; the run will then abort at B8 |
| ☐ B5 | — | **"Calibration — Step 2 of 2: Bright Frames"** — "Please turn on the laser and remove the subject…". Click **OK**. | |
| ☐ B6 | Watch | The image is bright again, **⟨I⟩ ≈ 121 DU**, label counts **"Bright cal: n / 600"**, then **"Cal OK — dark+bright done, saved Calibration.h5"**. First you may see **"Discarding N buffered frames…"** with N in the **hundreds or thousands** (1555 with 600 frames on the dev PC) — normal here: frames captured during the dark calibration that the PC had not reached yet, dropped so they cannot enter the bright one (todo D8) | |
| ☐ B7 | Watch | Label **"Normalizing — t / 5 s (… s left)"**. The plot stays **empty** for these 5 s — the curve can't be scaled until the window closes. | |
| ☐ B8 | Watch | Label **"Normalized ✓"**. The plot fills in, including the first 5 s. **κ², always positive: ≈ 0.0085 with 600 frames (1/κ² ≈ 115–120), ≈ 0.0070–0.0076 with 60 (1/κ² ≈ 130–145)** — see "normal" item 3. **"Dropped: 0".** This PC cannot process 2.4-Mpx frames at 40 Hz (the rig's 700 × 700 is ≈ 5× lighter), so expect **FPS roughly 15–25 instead of 40**. How far below 40 depends on this PC's load and the Workers setting: ≈ 21 was measured headless with 3 workers, and real windows drawing the image and plot can pull it lower. Also expect, about 1½ s into normalization, a status-bar message **"SCOS overload — input queue 16/20 full; camera capture is being throttled…"** that is gone within 2½ s (K4). That is the design working: the playback is slowed down rather than frames being thrown away (measured in rig prep 3b). | A **"Corrected κ² Is Negative"** error → the dark calibration saw light (B4 failed). **"Dropped" above 0** → something stalled processing for over 1½ s; note the number and the time |
| ☐ B9 | Let it run **at least 2½ minutes** | When the recording passes **120 s**, the plot's x-axis switches from **seconds to minutes** | |
| ☐ B10 | Click **Stop SCOS** | Pop-up **"Measurement Ended — Please turn off the laser."** Click **OK**. Playback switches to the dark folder for one frame to check the laser went off; in playback that check **passes silently** (no second pop-up). | A **"Laser May Still Be On"** pop-up → step 4, with the numbers it shows |
| ☐ B11 | Watch | Parameters unlock, button reads **Start SCOS**, the image is bright again (back on the recording). | Image stays dark → playback was not restored to the recording |

## C · What landed on disk

Open your folder in Explorer. Do not rely on the status bar for the path (K1). `app.log` in
the repo folder also has it: search "Session folder created".

| | Check | Expect |
|---|---|---|
| ☐ C1 | Folder name | `test_run__1_<YYYYMMDD>_<HHMMSS>` — the colon and each space became an underscore (hence the double one), timestamp always added |
| ☐ C2 | Files | exactly **`Calibration.h5`** (≈ 23 MB here — 1216 × 1936 arrays; much smaller on the 700 × 700 rig camera), **`rBfi_results.h5`** (tens of kB), **`rBfi_fig.png`** |
| ☐ C3 | `rBfi_fig.png` | Opens; shows the same curve as the plot, x-axis in minutes for a run over 2 min |
| ☐ C4 | `rBfi_results.h5` (open with HDFView, or ask Claude to print it) | datasets `startTime`, `timeVec`, `rBFi`, `Intensity`, `k2_raw`, `k2_corr`, `bfi`, groups `Params` and `metadata`. `Params` has ten fields: frameRate 40, exposureTime 5, gain 24, windowSize 7, ROI, bitDepth 10, normalizationConstant, normalizationMethod, normalizationWindowSec, gitCommit. **No `satCapacity` anywhere.** |
| ☐ C5 | Normalization | Run **over 2 min** → `normalizationMethod` = `mean` and rBFi hovers around **1**. (A run **under** 2 min uses `percentile5` instead, so rBFi sits mostly **above 1** — 1.64 on average in the 10-s headless run. Both are MATLAB's rule, `SCOSvsTime_WithNoiseSubtraction_Ver2.m:505`.) |
| ☐ C6 | `Calibration.h5` | groups **`dark`** (`mean_dark` ≈ 99.3 DU, `var_dark`, `mask`) and **`bright`** (`spIm` ≈ 22 DU inside the ROI, `spVar` ≈ 0.5 with 600 frames / ≈ 1.2 with 60), each with `n_frames` = your frame count |

---

## D · A second run in the same window

| | Do | Expect |
|---|---|---|
| ☐ D1 | Change the Recording name, click **Start SCOS** again | G pop-up, then **no folder dialog** — the folder chosen in B2 is reused for the whole window — then straight to the dark-frames prompt |
| ☐ D2 | Run it for ~30 s and stop | A **second, separate** session folder next to the first; the first one is untouched |

## E · A short run with auto-stop

| | Do | Expect |
|---|---|---|
| ☐ E1 | Set Measuring duration to **1** minute | Pop-up **"Short Recording Detected"** — switch to "Pulsation lower level"? Click **Yes**. The seconds box disappears. |
| ☐ E2 | Start SCOS, go through the prompts | After normalization a **"⏱ m:ss remaining"** label counts down from 1:00 |
| ☐ E3 | Don't touch anything | At 0:00 the run stops by itself and shows the same **"Measurement Ended"** pop-up as B10 |
| ☐ E4 | Check the file | `normalizationMethod` = `percentile5` |
| ☐ E5 | Set the duration back to **∞** (0) **and** Norm. type back to **Number of seconds** | Otherwise every later run silently uses the 5th percentile |

## F · Cancelling and stopping early

| | Do | Expect |
|---|---|---|
| ☐ F1 | *(Needs a fresh window — the folder dialog only appears once per window: close and relaunch.)* Start SCOS → **Cancel** the folder dialog | Nothing created; button back to Start SCOS; parameters unlocked |
| ☐ F2 | Start SCOS → **Cancel** at "Please turn off the laser" | The session folder that was just created is **removed again**; parameters unlocked |
| ☐ F3 | Start SCOS → OK → click **Stop SCOS while "Dark cal: n / …" is counting** | **Today: see K3** — image stays dark |
| ☐ F4 | Start SCOS → OK → **Cancel** at "Please turn on the laser" | **Today: see K2** — parameters stay locked. To unlock: run F5 to the end, or relaunch |
| ☐ F5 | Stop SCOS during **normalization** (before "Normalized ✓") | Run ends; files written; per open question 11 there is **no `rBFi`** worth using — note what you see |

## G · Close and relaunch

| | Do | Expect |
|---|---|---|
| ☐ G1 | Change Window Size, close, relaunch in `--mock-folder` | Window Size is back to the default — **by design**, settings are only remembered for the real camera (B3), so a recording's 24 dB never leaks into a rig session |

---

## Known issues found while writing this (→ step 4)

Found by reading the code on 2026-10-04, not yet seen on screen. The steps above are written
so you will meet each of them; confirm or refute.

- **K1 · Status-bar messages vanish.** Outside a measurement, every displayed frame writes
  "Frame #…" to the status bar (`gui/main_window.py:1595`, up to 30 times a second). So
  "Session folder: …" (1249), "Recording → …" and the closing **"Session finished → <folder>
  | <laser-off note>"** (965) are overwritten almost at once. The laser-off note is the only
  on-screen trace of a *skipped* laser-off check — Done item 28 says that message is the last
  one written; in practice the frame counter writes after it.
- **K2 · Cancel at the bright prompt leaves the parameters locked.** That branch
  (`gui/main_window.py:1386-1394`) and both calibration-error branches (1308-1316, 1415-1423)
  reset the button but never call `_set_params_enabled(True)`, as `_reset_start_button()`
  does. They also leave the session folder on disk with a `Calibration.h5` holding only the
  dark group.
- **K3 · Stopping during dark calibration leaves playback on the dark folder** (playback
  only). Neither Stop SCOS (920-925) nor Stop Video (757-759) during `DARK_CAL` switches
  playback back to `"main"`, so the preview stays dark until the next Start SCOS. Same class
  of bug that Done item 28 fixed for the laser-off check.

Found by the slowdown rehearsal (rig prep 3b, 2026-10-06), seen in a headless run:

- **K4 · The overload warning vanishes too** — same cause as K1. During a measurement
  `_on_display_frame` rewrites the status bar every 2.5 s, so "SCOS overload — input queue…"
  is gone within 2.5 s; 3 s after it fired the bar shows "Frame #…". "Dropped: N" (red, always
  visible) and `app.log` keep the record. Fix together with K1.
- ✅ *Fixed 2026-10-07 (todo Done item 35): the pipeline now drops that backlog instead of processing it.* ~~**K5 · Closing the window while processing is far behind leaves the pipeline running.**~~
  `RealtimePipeline.stop()` queues its stop marker *behind* the waiting frames, so the
  pipeline first processes the whole backlog (up to 20 queued + 6 in flight) — results
  nobody will use, since Stop SCOS already ended the session — while `closeEvent` waits only
  2 s. With processing slowed to ≈ 0.75 s a frame it needed 3.7 s more, and at Python's exit
  it died mid-task with "cannot schedule new futures after interpreter shutdown". The session
  files are already written by then. Needs processing slower than ≈ 230 ms a frame with 3
  workers; the rig's 700 × 700 frames are far below that.

## What this rehearsal cannot test

The real Basler camera — its lost-frame warnings and the camera-clock timestamps (todo D5) — external trigger and the
Arduino, a real laser and room light, the 700 × 700 / 20 Hz load, and an exact gain-table
match. Those belong to the real-rig checklist (step 5a).
