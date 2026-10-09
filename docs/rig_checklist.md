# Rig checklist — the first session on the real camera

Rig-session prep step **5a** (`docs/todo.md`). Written 2026-10-07 against commit `e8dcdc2`; used at
the first rig session on 2026-10-08 — what passed is in "Result of the first session" below.

This is the companion to [`simulation_checklist.md`](simulation_checklist.md). That one
rehearses the whole session on a recording. This one covers what a recording **cannot**
test: the real Basler camera, its clock and lost frames, the external trigger and the
Arduino, the lab PC's speed, and settings left behind on that PC.

How to read each step: **Do** → **Expect** → **If not**. Tick the boxes as you go.
Everything here was written from reading the code, not from a run on the rig — if a step
doesn't match, the checklist may be wrong rather than the app. Note it either way.

Expected values are for the lab camera's default settings — **Mono12, 8 ms exposure, 8 dB,
20 Hz, Window Size 7, Dark/Bright Frames 600**, and the rig camera's full frame, **1216 × 1936**
(rows × columns; 700 × 700 until 2026-10-08, when the rig camera turned out to send its full
sensor — the camera-side crop is todo U6) — unless a step says otherwise.

> **`app.log` is overwritten every time the app starts** (`main.py:28`, `mode="w"`). It is
> written to the folder you launched from — the repo folder if you follow the commands
> below. **Copy it somewhere safe before every relaunch**, e.g. into that run's session
> folder; most of the checks below are read from it.

---

## Result of the first session — 2026-10-08

Session `TestVika10082026_20261008_145048` (Vika as the subject, 1 min, External Trigger on,
20 Hz, rig camera a2A1920-160umBAS SN 40075248, USB). Checked on 2026-10-09 against that
session's `app.log`, `rBfi_results.h5` and `Calibration.h5`. **Code that ran: `0822db1`**
(`Params/gitCommit`) — before the BlockID fix `dffa06d`, so the current code has not run on
the camera yet. The ☐ boxes in the tables below are left empty so the list can be used
again at the next visit.

✅ checked in the files and OK · 👁 seen at the rig, not in any file · ⚠️ done, but not
the way the checklist says · ➖ not done

| Step | | What the files show |
|---|---|---|
| P1 | ✅ | SN 40075248 is in `CamerasMeasuredGain.csv` at Mono12, 8 dB ("DAN01", G = 0.9564) |
| R1a | ✅ | Ran `0822db1`, the latest commit that day |
| R1b | ➖ | Fast tests on the rig PC — not recorded |
| R1c | 👁 | `check_camera.py` before launch: one camera, a2A1920-160umBAS, SN 40075248, USB (output was in the chat, not saved) |
| R1d | ✅ | `app.log`: "Camera opened — model=a2A1920-160umBAS SN=40075248" |
| R1e | ➖ | Not checked that day. **For the next visit:** the app saved `scos_config.local.json` on close (15:05) with **External Trigger on, Measuring duration 1 min, Norm. type pulsation** — the next launch on this PC starts with those. Dark/Bright Frames are 600 in it |
| R1f | ➖ | Benchmark not run |
| R2a | ✅ | "Mode: real Basler camera", no errors (state-box colour not recorded) |
| R2b | ✅ | Start Video 14:45:33, IDLE → PREVIEW. The FPS label was not recorded; frame timing in the file is 50 ms (R6d) |
| R2c | ✅ | Start Video: Mono12, 8.0 ms, 8.0 dB, 20 Hz; Start SCOS: window 7; both calibrations collected 600 frames |
| R2d | ✅ | 14:45:39, 5 s after capture start: "Camera clock accepted — 1000 MHz ticks agree with the PC clock over 5.0 s". Also on the first frame: "Camera reports no frame numbers (BlockID 0)" — a false alarm (USB cameras number frames from 0), fixed in `dffa06d` (todo Done item 41) |
| R2e | ✅ | No "frame(s) lost" anywhere in `app.log` (the app logs every such warning) |
| R2f | ✅ | ROI saved in `Params`: cx 1220.9, cy 864.8, r 278.5 (Auto or Draw — not recorded) |
| R3a | ✅ | Arduino found on COM10, compiled and uploaded in 10 s: "Arduino ready — T=50 ms, exposure=8 ms on COM10"; "Trigger mode → On" |
| R3b | ⚠️ | Partly: the measurement ran on the trigger at 50 ms per frame (R6d), so the pulses reach the camera. Whether the FPS spinbox jumped — not recorded |
| R3c | ✅ | A second "Camera clock accepted" at 14:46:18, 5 s after Trigger On — and again after each grab restart in the calibrations (4 times in all) |
| R3d | ➖ | Not done (only one upload in the log) |
| R4 | ⚠️ | Different from the checklist, on purpose: Recording name "TestVika10082026"; Measuring duration 1 min; **Norm. type pulsation** (method percentile5 over the first 5 s) instead of "Number of seconds, 5 s". Both are valid settings |
| R5a | ✅ | "G = 0.956400 DU/e (source: table, SN 40075248, Mono12, 8.0 dB)" — exact row, no pop-up |
| R5b | ✅ | "Session folder created: …\TestVika10082026_20261008_145048" |
| R5c | ✅ | Dark prompt answered 14:50:58; trigger switched off for the dark calibration |
| R5d | ✅ | Really dark: `mean_dark` 0.46 DU (0.52 in the ROI) against ⟨I⟩ 113.9 DU with the laser on; 600 frames |
| R5e | ✅ | Bright prompt answered 14:51:59; trigger back on |
| R5f | ✅ | 600 bright frames |
| R5g | ✅ | Dark **30.2 s** (14:50:58.8 → 14:51:29.0), bright **30.1 s** (14:51:59.9 → 14:52:30.0) — the 30 s minimum, so the rig PC keeps up with full 1216 × 1936 frames at 20 Hz. "Flushing 2" / "Flushing 3" |
| R5h | ✅ | "Normalization window closed at 5.0 s … over 101 points"; κ²_corr > 0 in all 1300 frames (mean 0.0081); dropped 0 |
| R6a | ⚠️ | Ran 65 s, not "a few minutes". Within that: no overload, no lost frames, dropped 0. A longer run is still to do |
| R6b | ⚠️ | Only the failure path tested — the laser was left on on purpose: "Laser-off check FAILED — 114.5 DU, expected < 11.4 DU", the warning appeared, Continue kept the data. The normal path (laser really off → passes silently) has not been seen on the rig yet |
| R6c | ✅ | `app.log` is in the session folder |
| R6d | ✅ | `time_source = camera`, `frames_lost_camera = 0`, `frames_dropped_queue = 0`, `camera_sn = 40075248`. 1300 frames; time step median 50.015 ms, min 50.009, max 50.019 — no gaps |
| R6e | ✅ | All three files. `Calibration.h5` is **24.4 MB** — the "≤ 10 MB" in R6e was for 700 × 700 frames; `rBfi_results.h5` 116 KB for 65 s; `rBfi_fig.png` |
| R7 | ➖ | Deliberate overload not run (todo D5 step 3) |

**Still to do on the rig:** R1b, R1f, R3d, a run of several minutes (R6a), the laser-off
check with the laser really off (R6b), R7, and — with the current code — confirm the camera's
frame numbers (`BlockID`) count 0, 1, 2, … without the false warning.

---

## Before the trip — this can block the whole session

| | Do | Expect | If not |
|---|---|---|---|
| ☐ P1 | Find out **which camera is on the rig and its serial number** (ask Vika, or read the sticker / Pylon Viewer). Then look in `CamerasMeasuredGain.csv` for a row with that **SN** and **nBits = 12** (Mono12). | A row exists. Today the file has Mono12 rows for SN **40513592** (8 dB), **40075248** (8 dB), **40335410** (16, 20, 24 dB) and **24828238** (24 dB). | **No row for that SN at Mono12 → the app will refuse to measure** (step R5). This cannot be fixed at the rig: G[DU/e] must come from the measured table, never a formula (supervisor's ruling, 2026-09-22). Ask Vika for the measured G of that camera before going. |

---

## R · At the rig

### R1 · Before launching

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R1a | In the repo folder: `git log -1 --oneline` | The same commit you tested at home (`e8dcdc2`, or whatever step 4b ends on) | `git pull`, then check again |
| ☐ R1b | `venv\Scripts\python.exe -m pytest tests/ -m "not slow"` | All pass. Same set the pre-commit hook runs. | Note which test failed and the error; don't measure until it's understood |
| ☐ R1c | `venv\Scripts\python.exe check_camera.py` | `Found 1 camera(s): [0] <model>  SN:<number> …` and `Frame grabbed OK: shape=(…), …` | "No Basler cameras found" → cable, Pylon SDK, or Pylon Viewer still holding the camera (close it) |
| ☐ R1d | Compare that SN with P1 | Same SN, and its Mono12 row is in the table | Different camera than expected → redo P1 for this SN now |
| ☐ R1e | Look for **`scos_config.local.json`** in the repo folder | Either absent, or you know what's in it. This file holds the operator's last settings and **overrides the committed defaults** — e.g. Dark/Bright Frames 60 from an old test | Rename it to `scos_config.local.old.json` to start from the defaults; R2c checks the result |
| ☐ R1f | Benchmark, with the app **closed**: `venv\Scripts\python.exe bench_processor.py --width 1936 --height 1216 --window 7 --bits 12 --fps 20` (takes 30 s; width = columns, height = rows) | Last lines: `Budget at 20.0 Hz : 50.0 ms/frame`, **`Headroom … (OK)`**, and a `Max sustainable` figure. **Write down `mean` and `Max sustainable`** — R6 uses them. This is one worker; the app runs several in parallel, so its real capacity is higher. | `Overrun … (GUI will lag!)` → the lab PC is slower than expected. A session can still run (the app slows the camera rather than skipping frames), but tell me the numbers |

### R2 · Launch and preview

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R2a | `venv\Scripts\python.exe main.py` | First the **"Laser safety"** warning (goggles; probe off only after the red laser light is out, by pulling the rubber strap backwards) — read it to the subject, click **I confirm**; `app.log`: "Laser-safety warning confirmed by the operator" (since 2026-10-09; **Exit** closes the app). Then the window opens; status bar "Ready — camera not started"; state box **IDLE** (grey) | Traceback in the terminal → copy it |
| ☐ R2b | Click **Start Video** | Image appears; state box **PREVIEW** (light blue); status bar "Video running"; **FPS ≈ 20** | FPS well below 20 → see R2e |
| ☐ R2c | Look at the parameter boxes | Mono12, 8 ms, 8 dB, 20 Hz, Window Size 7, **Dark Frames 600, Bright Frames 600** | Different values → a `scos_config.local.json` is in play (R1e). Set them by hand |
| ☐ R2d | **About 5 s after** Start Video (the check needs 5 s of frames), open `app.log` and search "Camera clock" | **`Camera clock accepted — … MHz ticks agree with the PC clock`** (todo D5: the app then stamps each frame with the camera's own exposure time instead of the PC's arrival time) | **`Camera clock not used — <reason>`** → the session is still valid (PC timestamps, as before 2026-10-07), but copy the reason |
| ☐ R2e | Watch FPS and the status bar for a minute | FPS steady ≈ 20; **no** "Camera: N frame(s) lost" warning | "Camera: N frame(s) lost — Pylon buffers full or transfer failed (M since Start Video)" in preview already → cable / network adapter / PC load; note N and M |
| ☐ R2f | Set the ROI: **Auto ROI** or **Draw ROI** (drag and resize the circle) | The ROI label shows `cx=… cy=… r=…`; ⟨I⟩ updates | |

### R3 · External trigger and the Arduino

Skip if this session runs on the camera's internal frame rate.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R3a | Tick **External Trigger** (or press **v**) | Status bar "Arduino: connecting…", then **"Arduino: trigger pulses active \| exposure=8.0 ms, FPS=20.0 Hz"**; window title **"SCOS — Trigger ACTIVE (20 Hz, 8 ms)"**. The checkbox is greyed out while the upload runs. | "Arduino Upload" warning pop-up → copy its message; the checkbox unticks itself and the camera stays on internal timing |
| ☐ R3b | Watch | Image keeps updating; **FPS ≈ 20** (now set by the Arduino's pulses); the **FPS spinbox does not jump** to some other value | FPS 0 → pulses not reaching Line2. FPS spinbox jumping → note the values (an earlier bug of this kind was fixed) |
| ☐ R3c | `app.log`, search "Camera clock" again | A **second** "Camera clock accepted" a few seconds after the switch — changing trigger mode restarts grabbing, and the clock check starts over | "not used" → copy the reason |
| ☐ R3d | Change Exposure or FPS by one step, wait 2 s | A new Arduino upload starts by itself (≈ 1 s after the last change) and ends with "trigger pulses active" showing the new values | |

### R4 · Settings for the session

Type a **Recording name**. Measuring duration **∞** (or the planned length), Norm. type
**Number of seconds, 5 s**. Leave **External Trigger** as the session needs it: the dark
calibration switches it off by itself and puts it back afterwards.

### R5 · Start SCOS — the dialogs

The order is the protocol's (`SCOS_protocol.md:11-17`) and is asserted by a test.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R5a | Click **Start SCOS** | **G[DU/e]**, one of three outcomes: **(1)** SN + Mono12 + 8 dB is an exact row → **no pop-up**, straight on. **(2)** SN + Mono12 is in the table but not at 8 dB → **"Estimated G[DU/e]"** warning, "G was rescaled from the closest measured gain (G=… DU/e). SCOS will continue." Click OK. **(3)** Not in the table → **"Can't calculate SCOS: CameraSN … Mono12 was not found in G[DU/e] Calibration file"** — the run does not start. | (3) → P1 was missed. No measurement possible with this camera today |
| ☐ R5b | — | Folder dialog (first run of this window only). Pick the results folder. | |
| ☐ R5c | — | **"Calibration — Step 1 of 2: Dark Frames"** — "Please turn off the laser." Turn the laser **off**, make the area dark, click **OK** | |
| ☐ R5d | Watch | Parameters locked (dark grey); **⟨I⟩ drops clearly** and stays steady; label **"Dark cal: n / 600"**. Takes **at least 30 s** (600 ÷ 20 Hz); about 30–40 s if the lab PC keeps up (estimated, todo D8), longer if it doesn't. With Dark Frames 60: ≈ 3–5 s. If the trigger was on, it is switched off for this step. | ⟨I⟩ does not drop → light still reaches the sensor; Stop SCOS and start again (otherwise the run aborts later, R5h) |
| ☐ R5e | — | **"Calibration — Step 2 of 2: Bright Frames"** — "Please turn on the laser. Keep the subject in the measurement area. … The measurement starts automatically when this calibration ends." Turn the laser **on**, **leave the subject where it is**, click **OK** | |
| ☐ R5f | Watch | ⟨I⟩ back up; **"Bright cal: n / 600"**, ≈ 30 s again; then **"Cal OK — dark+bright done, saved Calibration.h5"**. "Discarding N buffered frames…" may flash first — normal. | |
| ☐ R5g | **`app.log`**: how long each calibration took. Dark = from **"State: PREVIEW → DARK_CAL"** to **"Dark calibration complete — 600 frames collected"**; bright = from **"State: PREVIEW → BRIGHT_CAL"** to **"Bright calibration complete"**. Compare with 600 ÷ FPS = **30 s** at 20 Hz | **About 30–35 s each** → the lab PC keeps up; nothing to do | **Clearly longer** (e.g. 45 s or more) → the collector is slower than the camera, and **todo D8** (faster dark-calibration code) moves up the list. Write both times down. Don't judge by "Flushing N" in the log: the open pop-up keeps draining frames, so N stays small even on a slow PC (N was 2 in the hand-run playback, where dark took 65 s against a 15 s minimum) |
| ☐ R5h | Don't touch anything. At "Cal OK" the app goes **straight** into the measurement, with no pop-up, and the **normalization baseline (the first 5 s) starts at that moment** — so the subject must already be in place and still, which it is when it stayed there for the bright calibration. Watch | "Normalizing — t / 5 s (… s left)", plot empty; then **"Normalized ✓"** and the plot fills in. κ² positive. **"Dropped: 0"**. FPS ≈ 20. | **"Corrected κ² Is Negative"** → the dark calibration saw light (R5d) |

### R6 · Run, stop, and check the file

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R6a | Let it run a few minutes | FPS ≈ 20 the whole time; **no** "SCOS overload" message; **no** "Camera: N frame(s) lost"; Dropped stays 0 | An FPS below 20 with no warning is itself worth noting — copy the time |
| ☐ R6b | **Stop SCOS** → "Measurement Ended — Please turn off the laser." Turn it off, OK | No warning; status bar **"Session finished → <folder>"**; then the **green** window **"Laser is off — you may remove the probe"** (since 2026-10-09): check the laser's red indicator light is not lit, remove the probe by pulling the rubber strap backwards, OK | The **red "LASER MAY STILL BE ON"** window → is it? **Check again** measures again; **Continue anyway** keeps the data and is followed by a red "do NOT remove the probe yet" window. An **amber** "could not run" window → copy the closing status message |
| ☐ R6c | **Copy `app.log`** into the session folder | — | |
| ☐ R6d | Check the results file (or ask Claude to): `venv\Scripts\python.exe -c "import h5py,numpy as np,sys; f=h5py.File(sys.argv[1]); print(dict(f['metadata'].attrs)); t=f['timeVec'][()].ravel(); d=np.diff(t)*1000; print(t.size,'frames; dt ms median %.2f min %.2f max %.2f' % (np.median(d), d.min(), d.max()))" "<session folder>\rBfi_results.h5"` | In `metadata`: **`time_source = camera`**, **`frames_lost_camera = 0`**, **`frames_dropped_queue = 0`** (both counted from Start SCOS), and `camera_sn` = your camera. Time steps: **median ≈ 50 ms**, min and max close to 50 ms | `time_source = pc` → R2d said why. Any lost/dropped > 0, or a max step of 100, 150 ms… → frames went missing; note the numbers |
| ☐ R6e | Folder contents | `Calibration.h5` (≈ 25 MB at 1216 × 1936 — 24.4 MB on 2026-10-08), `rBfi_results.h5` (≈ 3.5 MB per hour, an estimate not yet checked on a long run; 116 KB for 65 s on 2026-10-08), `rBfi_fig.png` | |

### R7 · Deliberate overload — **last, on a throwaway session**

Purpose: see the lost-frame counting work on the real camera (todo D5, step 3). It changes
settings that the app **saves on close** — see R7f before closing the window.

| | Do | Expect | If not |
|---|---|---|---|
| ☐ R7a | External Trigger **off**. Set **Processing workers = 1**, **Exposure 2 ms**, and FPS to about **twice R1f's "Max sustainable"** (or the highest the camera accepts) | FPS label rises above "Max sustainable". The camera itself caps the rate (≈ 1 ÷ exposure, and the USB link), so if FPS won't go higher, use what you get | FPS stays at or below "Max sustainable" → this PC can't be overloaded this way; skip R7 and note it |
| ☐ R7b | Start SCOS, go through the prompts. **Keep Dark/Bright Frames at 600** — at an overload rate 60 frames would finish in about a second, before the camera-clock check (5 s) is done, and the run would mix PC and camera timestamps | During the run: status bar **"SCOS overload — input queue N/20 full; camera capture is being throttled to keep up (at HH:MM:SS)"** | |
| ☐ R7c | Keep it going ~30 s | **"Camera: N frame(s) lost — Pylon buffers full or transfer failed (M since Start Video)"**, at most once a second; the label reads **"Dropped: N + M lost at camera"**; the **GUI stays responsive** (the grab loop waits, the window doesn't freeze) | GUI freezes → note how long. No lost-frame warning although FPS shown < FPS set → copy both numbers |
| ☐ R7d | Stop SCOS, copy `app.log` | — | |
| ☐ R7e | R6d's command on this session's file | `frames_lost_camera` > 0. The large time steps are **whole multiples** of the frame period (a lost frame leaves a gap of its true size; frames are not bunched together) | Steps that are *not* multiples, or bunches of very short steps → copy the output |
| ☐ R7f | **Before closing the window:** set Processing workers (3), Exposure (8 ms) and FPS (20 Hz) back. Close, relaunch, check R2c | Defaults back | Otherwise the next session starts at the overload settings — the app remembers the last settings for the real camera |

---

## What to bring back

- every `app.log` you copied (R6c, R7d);
- R1f's benchmark output;
- the two calibration durations from R5g;
- R6d's printout for the real session and R7e's for the overload one;
- anything that didn't match, with the time it happened.
