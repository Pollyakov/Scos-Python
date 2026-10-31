# SCOS — Implementation Backlog

Last updated: 2026-10-20 — **this is now the only working task list.** It absorbed the open
items of `docs/reviews/merged_worklist.md`, which is frozen as an archive of the review
evidence and the reasoning behind each task (map at the bottom of this file).

Order: what to do next (fixes from the first rig session, then what is left of rig-session
prep) → open backlog by priority → done.

---

## ▶ Next: fixes from the first rig session (2026-10-08)

The user's list after the first session on the real rig (camera SN 40075248, USB, Vika as the
subject; folder `TestVika10082026_20261008_145048`). Order: the subject's safety first, then
anything that can hide a warning, then usability, then the results files (figure, Save Data
button, file name — U8 and U9 added the same day, from syncing `SCOS_protocol.md` with Vika's
text), and last the changes that touch the measurement path: the camera-side crop (U6) and
its record in the results file (U10), then a faster dark calibration (U11), which needs the
faster collector D8 first — U6, U10 and U11 come from a conversation with Vika the same
evening.

**Pending decisions (2026-10-19 rig day):**
- **Auto-stop loses one result — decided: not fixing (user, 2026-10-19).** When the set
  duration is reached, `_on_scos_result()` calls Stop SCOS and returns *before* recording the
  result that crossed the limit; results already in flight are still recorded during the
  laser-off prompt (the stop path sets `_measuring_start_time = None`), so `timeVec` has one
  hole one frame before its end (session `rig_full_600/scos_20261019_200135`: 100.06 ms at
  64.99 s; `check_session.py` NOT OK). One frame in a run; a manual Stop SCOS is unaffected.
  Fix if ever wanted: record first, then stop.
- **`check_session.py` judges timing against the *set* frame rate.** With FPS set above what
  the camera can deliver (R7: 150 Hz set, 76.5 fps delivered) it prints 5 false NOT OK rows
  (time step, dark duration, dark rate) although every step was exactly one camera period.
  Open: teach the tool the delivered rate, or keep the note in `rig_checklist.md` R7e
  (recommended — it happens only in the overload test).

**Pending decisions (2026-10-22 rig day — camera SN 40513592):**
- ~~**Stale Arduino program goes unnoticed.**~~ **Decided 2026-10-25: both** (send the
  program at launch, and warn on a frame timing that does not match the set rate) — done,
  Done item 60.
- ~~**No laser-off check before the dark calibration.**~~ **Decided 2026-10-26: check it, and
  stop the run when it fails** (not "check again", not "continue anyway") — done, Done item 61.

| # | Task | What it is | Status |
|---|---|---|---|
| U1 | Laser-safety warning at startup | When the app starts, show a safety warning that the operator must confirm before continuing: **the subject must wear laser-safety goggles for the whole measurement; the probe may be removed only after checking that the laser is off — the red indicator light on the laser is not lit; remove the probe only by pulling the rubber strap backwards.** | ✅ 2026-10-09 — Done item 42 |
| U2 | Unmissable "laser still on" warning | At the end of a measurement the app asks the operator to switch the laser off, then checks the camera image (`MainWindow._laser_off_check()`, E3). If the operator clicks OK but the laser is still on, the existing "Laser may still be on…" dialog appears — but it looks like any other dialog box. Make it impossible to overlook: red, large bold text, a warning icon. Tested on the rig 2026-10-08 by leaving the laser on on purpose: the check fired correctly (114.5 DU, expected < 11.4 DU). | ✅ 2026-10-09 — Done item 43 (with a probe-removal window after the save, the user's idea) |
| U3 | App window larger than the screen | The window does not fit on the computer screen, so part of it is cut off — e.g. the status bar in the bottom-right corner, where status messages and warnings appear. Resizing fixes it only briefly: after a few seconds the window grows past the screen edge again. The window must always fit the screen with everything visible. A window that grows back by itself is usually pushed by its contents (e.g. a label whose text gets longer) — finding what pushes it is part of the fix. Ranked high because a hidden status bar hides warnings. | ✅ 2026-10-10 — Done item 44 (nothing was pushing it: the control panel was taller than the screen; same cause as U4) |
| U4 | Crowded parameter fields | The parameter input fields are packed too tightly and some overlap. Rearrange them so every field and its label are fully visible and easy to read. | ✅ 2026-10-10 — fixed with U3, Done item 44 (fields two to a row) |
| U5 | Intensity plot + parameters box in the results figure | **Required by the SCOS protocol, step 6g** (Vika's request): *"Create a figure with two axes: rBfi vs time and \<I\> vs time. Add text box with all parameters used. Save into figure file (in python format). (TBD)"*. "Two axes" = two separate plots side by side in one figure: rBFi vs time and mean intensity ⟨I⟩ vs time. Today `rBfi_fig.png` shows rBFi only. Add the ⟨I⟩ plot (the data is already saved as `Intensity`) and a text box with every parameter used (exposure, gain, frame rate, pixel format, dark/bright frame counts, normalization method and window, camera SN, …). The "python format" part is **F5** (reopenable figure). *(The note that step 6g was missing from this repo's `SCOS_protocol.md` is out of date — the copy was synced on 2026-10-08.)* | ✅ 2026-10-10 — Done item 45 (stacked, not side by side — the user's choice) |
| U8 | Remove the "Save Data..." button | Protocol Notes (synced 2026-10-08): *"Remove the 'Save Data' button (data is always saved). Done"* — but the button is still in the app (`gui/main_window.py:371`, `_save_data()` exports κ² to `.mat`/`.npz` with keys `scosTime`, `scosData`, …). Session data is saved automatically, so nothing is lost by removing it. Remove the button, `_save_data()` and its wiring (`:681`, `:879`, `:966`, `:1009`); update `tests/test_calibration_exits.py:115,128` (asserts the button is re-enabled) and the two `_save_data()` tests in `tests/test_normalization.py:456,475` (they check the exported time unit — make sure what they protect is still covered by the results-file tests); fix the comment in `gui/plot_widget.py:149`; remove the "Save SCOS Data" line from CLAUDE.md. || ✅ 2026-10-11 — Done item 47 |
| U9 | Results file name per protocol | Protocol step 6: save into .h5 file **"rBFi_resultsAndParameters"**. The app writes **`rBfi_results.h5`**. Rename to `rBFi_resultsAndParameters.h5`. The name is set in one place (`gui/main_window.py:1092`; also the comment at `:1800`); `--mock-h5` takes a path, so it is unaffected. Update `tools/rehearsal.py`, the tests that open the file (`test_figure_export.py`, `test_invalid_k2_guard.py`, `test_normalization.py`, `test_results_schema.py`), and the docs that name it (CLAUDE.md, `expectations.md`, `rig_checklist.md`, `simulation_checklist.md`, `open_questions.md`; leave the frozen `reviews/merged_worklist.md` as it is). Sessions recorded before the change keep the old name — anything that reads results files later must accept both. || ✅ 2026-10-11 — Done item 48 |
| U6 | Crop on the camera when "Cut Image" is clicked | **Decided with Vika 2026-10-08:** when the operator clicks **"Cut Image"**, the camera itself starts sending only the cropped region, for the live display and the SCOS processing alike. The dark and bright calibrations are recorded **after** that (the operator picks the ROI before Start SCOS), so they have the same size and position as the measurement frames by construction. *(Vika's MATLAB did it the other way round, for the operator's convenience — fewer laser off/on switches: dark frames at full size, then the two dark matrices cut to the ROI's size and position. Both match; the GUI's order is simpler.)* **Today "Cut Image" only zooms the display** (`gui/image_widget.py:207`, `_apply_cut()`: a square around the ROI circle + 20 px margin); the camera always sends the full sensor (1216 × 1936 on 2026-10-08) and processing uses the full frame — Vika assumed otherwise. **To do:** send the crop to the camera with the existing `camera.set_roi()` (sets `OffsetX/Y`, `Width/Height`, restarts grabbing — nothing calls it today); round to the camera's allowed increments; rebuild the ROI mask in the cropped coordinates; "Full Image" goes back to the full sensor. **Accuracy rule:** the crop must not change between calibration and the end of the measurement — disable "Cut Image"/"Full Image" from Start SCOS on (as the ROI is already locked). Gain: far less data per frame → faster processing, lower risk of lost frames. Check the offline MATLAB tests still pass. | ✅ Code 2026-10-11 — Done item 49 (plan and per-step results: [`docs/Plan_U6_Camera_Crop.md`](Plan_U6_Camera_Crop.md) §6). **Rig ✅ 2026-10-19** (trip sheet [`rig_trip_U6_U10_U11.md`](rig_trip_U6_U10_U11.md) part B): the camera accepted the crop — 672 × 667 at (988, 470) in `TestKsenia/scos_20261019_130606` (600 + 600, 0 NOT OK), 460 × 458 at (1092, 614) in `BlackLevels/scos_20261019_161139`; FPS stayed 20, a new "Camera clock accepted" after each cut, one image area for results, dark and bright |
| U10 | Save the crop in the results file | **Vika, 2026-10-08:** record the crop that was used — **width, height, OffsetX, OffsetY** (plus the full sensor size) — as a group of values in the results file's `metadata` (her suggestion: "a struct with metadata"). The values are fixed when "Cut Image" is clicked; no crop → record the full frame. Suggested: the same values in `Calibration.h5`, so a calibration file always shows which crop it belongs to. Needs U6. Update `tests/test_results_schema.py`. | ✅ 2026-10-11 — Done item 50; **rig ✅ 2026-10-19**: `metadata/crop`, `dark/crop` and `bright/crop` equal in every session that day, cut and full (`check_session.py` "same image area") |
| U11 | Dark calibration at the camera's maximum frame rate | **Vika, 2026-10-08:** the frame rate does not matter for dark frames, so during `DARK_CAL` let the camera run as fast as it can. The camera switch she mentions is **`AcquisitionFrameRateEnable`** (already used at `camera.py:129`, `:270`): `False` = the camera runs at its maximum rate for the current exposure (dark cal already runs with the trigger Off); set it back to `True` with the chosen rate when the dark calibration ends. **Exposure and gain must stay exactly as in the measurement** — only the rate changes, because the dark noise depends on the exposure. **Limit — the collector:** on the rig PC the dark calibration took 30.2 s for 600 full-size frames (14:50:58.8 → 14:51:29.0, 2026-10-08), i.e. it kept up with 20 Hz, so it needs ≤ 50 ms per frame — how much less is not measured. If the camera outruns it, frames pile up in Qt's queue (unbounded during `DARK_CAL`, see CLAUDE.md) and the calibration does not finish sooner. So **D8 first** (or together), and measure the collector's per-frame time on the rig PC. U6's smaller frames help too. *(D8 done 2026-10-11, Done item 51: the dark collector alone ≈ 2× faster, 31 → 15 ms per full frame on the dev PC. On a 611 × 611 crop — the rig ROI of 2026-10-08 after Cut Image — it takes 1.4 ms per frame, so after a cut the collector is no longer the limit; the camera's own maximum rate is. Measure the rig PC with `tools/bench_dark_collector.py [--width W --height H]`, app closed.)* | ✅ code done 2026-10-12 — Done item 52, [`Plan_U11_Dark_Cal_Max_Rate.md`](Plan_U11_Dark_Cal_Max_Rate.md); **rig, 2026-10-19:** 600 dark frames in **4.8 s at 124 fps** cut (672 × 667; 8 ms exposure is the limit) and in **11.9 s at 76.5 fps** full (USB is the limit; 309 frames skipped while the GUI thread caught up, the collector still got 600), the 20 Hz rate restored every time, exposure and gain unchanged in the "Frame-rate limit off" line. That the maximum rate gives the same dark calibration was **already checked by Vika** — no rig A/B test needed (user, 2026-10-20) |
| U12 | Make the Save Frames checkbox easy to see | Rig, 2026-10-22: the operator forgot to tick **Save Frames** in two sessions running (the tick is deliberately not remembered between launches — saving fills the disk fast, ≈ 5.7 GB per minute on the full frame). Change the UI so the box stands out — where it sits, its size or colour, or a visible state next to Start SCOS; how is to be decided with the user. The tick itself stays not remembered. | ✅ code done 2026-10-27 — Done item 63. Rig check: does the operator notice it? (`rig_checklist.md` R8e) |
| U7 | CLAUDE.md: frame size not fixed at 700 × 700 | Removed the "700 × 700" frame size; camera is "Basler (USB or GigE)", the rig camera is USB; the placeholder "Lab demo camera" row replaced by the rig camera (SN 40075248). | ✅ 2026-10-08 |

---

## ✅ Rig-session prep (before the first session — closed 2026-10-20)

*Closed 2026-10-20:* the real rig sessions of 2026-10-08 and 2026-10-19 took the place of
what was left (3f, 4b); only **3d** stays open, and it is now about long recordings, not the
rig day. The text below is kept as it was written before the first session.

The first full session on the real rig is a couple of days away and the lab is not
reachable before then. Everything here either prevents a failure on the day or tests, in
simulation, the machinery the day depends on. Nothing in Tier C, B1, D1–D4, D7 or F goes
in before the session — every change adds risk. **E5 (tag v0) comes after a successful
session, not before.**

| # | Task | What it is | Est. | Actual | Status |
|---|---|---|---|---|---|
| 0 | Message to Vika | Open questions 7–16 from `docs/open_questions.md`, implemented defaults stated so she can just confirm; flag #11 (stop before the normalization window closes → no `rBFi`). Confirm the session's gain (dB) and bit depth match a row of `CamerasMeasuredGain.csv`. | 20 min | — | ✅ Sent 2026-10-04 |
| 1a | Docs ↔ code audit | todo, worklist, CLAUDE.md, protocol typo (bright cal said "turn off") | — | — | ✅ `7c89dbc`, `794d384` |
| 1b | **B3** — persist GUI settings | see B3 below; also remember `output_root` (dialog still shown) | 1 h 45 m | — | ✅ Done item 30 |
| 1c | ~~**B2-lite** — disk pre-flight~~ → **Save Frames disabled** | Dropped 2026-10-04: without raw frames a session writes < 50 MB even over 3 h, so a free-space check would almost never fire. The one way to fill a disk was the "Save Frames" checkbox (~70 GB/h at 20 Hz) — now greyed out until F1; raw frames are not wanted yet. B2 returns together with F1. | 10 min | ~25 min | ✅ Done item 31 |
| 2 | Simulation checklist | what the operator does and should see in a `--mock-folder` session, dialog by dialog; the expected (low) mock κ²_corr — the bright cal there comes from a subject-in-place recording, spVar ≈ 2.3× too large, which is the dataset, not a bug *(**corrected 2026-10-08:** wrong — the bright cal is taken with the subject in place, as MATLAB's was; with 600 frames playback matches MATLAB's 0.0084, and 0.0105 was not MATLAB's figure — `simulation_checklist.md` item 3)* | 45 min | ~45 min | ✅ [`simulation_checklist.md`](simulation_checklist.md) — numbers from a headless run 2026-10-04 (κ²_corr 0.0070 vs MATLAB 0.0105, positive 304/304); calibration frames set to the protocol's 600 (`683da41`) |
| 3a | Rehearsal harness in `tools/` | promote the headless `e2e_rehearsal.py` (2026-09-28 scratchpad; a copy patched to auto-answer the E3 "Measurement Ended" pop-up, without which it hangs, ran on 2026-10-04) to a committed, parameterised script with assertions and a scenario flag | 1 h 15 m | ~30 min (+ ~20 min follow-up docs) | ✅ Done item 32 — [`tools/rehearsal.py`](../tools/rehearsal.py) — every dialog stubbed and answered by title (unknown ones fail, never hang), watchdog `--timeout`, isolated config, ~30 checks (dialog order, laser-off check, dark cal from dark frames and bright cal from laser-on ones, files, Params, lengths, κ² > 0). Scenarios are hooks in `SCENARIOS`; only `normal` so far |
| 3b | Scenario: slowdown / backpressure | slowed `process()`: `overload_detected` once per episode, drops counted and visible, memory flat, **`timeVec` keeps capture cadence** | 1 h 30 m | ~1 h (2026-10-06 21:05–22:00; approximate) | ✅ Done item 33 — `--scenario slowdown`; all its checks pass except two real findings left failing for 4a (**K4**, and **K5** — fixed 2026-10-07, Done item 35; now only K4 fails); plus a D5 note on how the real camera stamps frames under overload |
| 3c | Scenario: overload recovery | restore speed: flag re-arms below 50 %, drops stop | 45 min | ~50 min (2026-10-07 ≈ 13:10–14:01, incl. the K4-check fix; approximate) | ✅ Done item 36 — `--scenario recovery`, all checks pass; plus a unit test for the 50 % mark, and the 3b K4 check made deterministic (it had passed by luck once) |
| 4c | rBFi on an early stop (open question 11) | **Vika's answer of 2026-10-07 changes existing behaviour.** Stop SCOS before the normalization window closes currently writes raw `bfi` but **no `rBFi`**. Her answer: normalize on whatever data exists. Build: at stop in `MEASURING_INIT`, compute the constant from `_bfi_norm_buffer` (method still chosen by `choose_norm_method` on `timeVec(end)`), write `rBFi`, and draw the buffered points before `render_now()`/`save_png()` so the figure is not blank. The window written to `Params` must be the span **actually used**, not the spinbox's `norm_seconds`. No finite BFi at all → still no `rBFi`, keep that warning. Update tests that expect "no rBFi" on an early stop, `simulation_checklist.md`, and what 5b says about stopping early. Matters at the rig: a short test run stopped early is exactly this case. **Done before 3d** (user's decision 2026-10-07) | 45 min | ~40 min (2026-10-07, approximate) | ✅ Done item 37 — `MainWindow._normalize_early_stop()`; 6 new tests (`TestEarlyStop`), rehearsal `normal` passes |
| 3d | Scenario: compressed long run | looped playback for minutes: recorder flushes, memory flat, plot responsive | 1 h + run | | ⏸ **Postponed until after the rig session** (user's decision 2026-10-07): the rig session and Vika's test will be short. Do it before any recording of more than a few minutes. The open risk it would measure: `gui/plot_widget.py` redraws **every** point once a second on the GUI thread, ≈ 216 000 points after 3 h at 20 Hz, at a cost nobody has measured |
| 3e | Output verification | after every scenario: `rBFi` present, length = `timeVec`, all ten `Params`, figure, both `Calibration.h5` groups | 45 min | | ~15 min (2026-10-07 ≈ 16:11–16:26; approximate) | ✅ Done item 38 — the presence checks already existed since 3a; 3e added checks that the **numbers** are right (rBFi = bfi / constant, constant recomputed from the file, metadata, ROI, startTime, calibration images); 3 new mutations each caught |
| 4a | Fix what turns up before 3f | K1–K3 plus anything 3b–3e find. Found while writing step 2 (`simulation_checklist.md`, "Known issues"): **K1** status-bar messages — session folder, the closing "Session finished … \| laser-off note" — are overwritten at once by the per-frame "Frame #…" text; **K2** Cancel at the bright prompt (and both calibration-error paths) leaves the parameters locked and a dark-only `Calibration.h5` on disk; **K3** Stop during `DARK_CAL` leaves playback on the dark folder (playback only). Found by 3b: **K4** the overload warning is overwritten the same way (≤ 2.5 s on screen) — fix with K1; `--scenario slowdown`'s K4 check is the acceptance test, and it watches `status.messageChanged` for the word "overload" — if the fix moves the warning to its own label, move the check with it, or it reports "(never shown)"; ~~**K5** closing the window while processing is far behind leaves the pipeline thread running past `closeEvent`'s 2 s (it drains a backlog nobody will use)~~ ✅ fixed 2026-10-07, Done item 35 | 2 h | | ✅ Done item 39 — K1–K4 fixed: the frame readout has its own status-bar field, so messages stay; every early exit from calibration goes through `_abandon_calibration()` (unlocks, playback back to the recording, a partial-calibration folder renamed `…_cancelled` by the user's choice); `slowdown` now passes every check, `normal` and `recovery` too; new `frame-text` mutation caught |
| 3f | **Hands-on GUI pass** (user) | follow [`simulation_checklist.md`](simulation_checklist.md) with real windows — the only test of the real modal-dialog path (nested event loop). Runs **after** 4a, on the code that goes to the rig | 1 h 15 m | | ✅ Closed 2026-10-20 — sections A–C done by hand on 2026-10-07 (folder `test run 1)/scos_20261007_173414`, files all correct); the rest dropped by the user on 2026-10-08 in favour of testing on the real rig, which happened 2026-10-08 and 2026-10-19 |
| 4b | Fix what 3f turns up | then re-run the 3a harness to show nothing else broke | ~1 h | | ✅ Closed 2026-10-20 — 3f's part found only checklist corrections ("Flushing 2", not hundreds; the overload message 3×), made in `simulation_checklist.md`; what the rig sessions found became U1–U11 above, all done |
| 5a | Real-rig checklist | first five minutes at the rig: **D5** ("Camera clock accepted" in `app.log`, `time_source = "camera"` in the results file, lost-frame counting and intake under a deliberate overload — new code since 2026-10-07), trigger-mode restart, Arduino, and `bench_processor.py --width 700 --height 700 --window 7 --bits 12` on the lab PC; **confirm Dark/Bright Frames read 600** on the rig PC — the committed default went from 60 (a 2026-09-26 test shortcut) back to the protocol's 600 on 2026-10-04, but a `scos_config.local.json` left on that PC would override it; **after the bright calibration, find "Flushing N frame(s) captured before the laser was switched back on" in `app.log`** — N in the tens means the lab PC keeps up with the dark collector and there is nothing to do; hundreds or thousands means it is slower than expected and **D8** moves up (in playback on the dev PC N was 1555, see D8) | 45 min | | ✅ Used at the rig 2026-10-08 and (with the short sheet [`rig_trip_U6_U10_U11.md`](rig_trip_U6_U10_U11.md)) 2026-10-19. Rows never done there: R1b (fast tests on the rig PC), R3d (Arduino re-upload after a change), R6a (a run of several minutes — the longest so far is 2.3 min, 2026-10-19 13:06). 📝 Written 2026-10-07 — [`rig_checklist.md`](rig_checklist.md). *2026-10-08: the "Flushing N" check (R5g) was replaced by calibration **timing** — the hand-run session showed N = 2 even with the dark collector 4× slower than the camera, because the real pop-up drains the backlog; N cannot reveal a slow PC.* Originally from reading the code (not run on the rig). Adds a **pre-trip blocker P1**: the rig camera's SN + Mono12 must be in `CamerasMeasuredGain.csv` — which camera is on the rig is not recorded anywhere in the repo. Also: `app.log` is overwritten at every launch (copy it), the benchmark needs `--fps 20`, and the overload test goes last because its settings are saved on close. **Used at the rig 2026-10-08** (first session, Vika as the subject, 1 min): P1 resolved — the rig camera is SN 40075248 (USB), in the gain table at Mono12/8 dB (G = 0.9564); Arduino on COM10 uploaded; dark cal 600 frames in 30.2 s (mean 0.46 DU — really dark), bright cal 600 in 30.1 s; "Flushing 2" / "3"; κ²_corr mean 0.0081, all 1300 > 0; rBFi shows a clear pulse at 1.53 Hz (≈ 92 bpm) plus 11 isolated one-frame spikes (unexplained, < 1 %); laser-off check tested by leaving the laser on — fired correctly (114.5 DU vs < 11.4). D5 steps 1–2 passed, step 3 (overload) and the benchmark not run. The user's fixes from it: U1–U11 above |
| 5b | Vika's expectations sheet | "what you'll see and why it's normal": calibration looks frozen except the counter, `Discarding N buffered frames…`, the G warning, the laser-off window + 90 % check, what stopping early does; and (added 2026-10-07) what the results file now also records — `metadata` → `time_source` ("camera" = the camera's own exposure timestamps, "pc" = the PC clock), `frames_lost_camera`, `frames_dropped_queue` — and the "Dropped: N + M lost at camera" label | 45 min | | 📝 Drafted 2026-10-07 — [`expectations.md`](expectations.md), kept up to date since (U6/U10/U11 notes; the A/B promise removed 2026-10-20). **Not sent to Vika** — send it when she is to run the app herself |

**Order changed 2026-10-05 (user's decision):** the hands-on pass 3f now comes after the automated scenarios and the K1–K3 fixes, so it tests the code that will actually go to the rig — fixing K1–K3 after it would change exactly the dialogs and status messages it checks. Remaining estimate ≈ 11 h 20 m, about 1 h 15 m of it hands-on. If time runs short, 3d goes first. **3d postponed 2026-10-07** (short recordings only at the rig), so the next step is 3e. **4c added 2026-10-07** (Vika's answer to open question 11) and placed before 3d, by the user's decision. **Actual** is filled in by `/wrap-up` (approximate, from session and commit times).

---

## ❌ Open backlog — ordered by priority

---

### Tier A — Measurement Correctness (do before anything else)

These items affect the scientific validity of results. Per the CLAUDE.md scientific priority
policy, correctness beats everything.

---

#### ~~A1 · `shrink_mask_for_window`~~ — ✅ DONE (see item 15 in Done table)

---

#### ~~A2 · `GrabStrategy_OneByOne`~~ — ✅ DONE (see item 16 in Done table)

---

#### ~~A3 · Cap in-flight work; don't just block `put()`~~ — ✅ DONE (see items 7 and 20 in Done table)

> Both halves have landed: `_inflight_sem` caps submitted-but-uncollected work
> (`core/pipeline.py:208`), and intake moved to the camera thread against a bounded
> blocking queue with `overload_detected` at 80 % (item 20). **Tier A is therefore
> complete.** The history below is kept because it records why the obvious fix was the
> wrong one.

**Why it matters:** `_DropOldestQueue` (in `core/pipeline.py`) is bounded and its drops are
counted — but that was never the actual leak. The `ThreadPoolExecutor`'s internal queue and
the `_inflight` deque behind it (`core/pipeline.py`, dispatcher loop) had **no size limit at
all**. A sustained processing slowdown grew those two without bound while `dropped_count`
kept reading zero — over an hour that is an out-of-memory crash that loses the whole
session, with no warning. Reproduced experimentally in `Review_Findings_B.md`.

**This doc previously said** "replace `_DropOldestQueue` with a blocking `queue.Queue(20)`."
**That fix is insufficient on its own** — a fast dispatcher empties a blocking `queue.Queue`
into the still-unbounded executor queue just as fast as a drop-oldest one. It also can't run
on the GUI thread (a full blocking queue would freeze the UI, since intake currently happens
in `_on_scos_frame`); moving intake off the GUI thread is tracked separately (see
`Implementation_Plan.md §2` and the pipeline docstring in `core/pipeline.py`).

**Fix implemented:** a `threading.Semaphore` (`_inflight_sem`, default `2 * n_workers`)
caps how many frames may be submitted-but-not-yet-collected at once. The dispatcher thread
(not the GUI thread) blocks on this semaphore before calling `pool.submit()`; the emitter
releases it after each future resolves. While the dispatcher is blocked, new frames still
land in `_input_q`, whose bounded drop-oldest behavior (and `dropped_count`) absorbs the
backlog visibly instead of letting it grow silently downstream. `dropped_count` is now
surfaced in the GUI Info panel (`Dropped: N`) and logged to `app.log` when it changes.

~~**Still open:** moving frame intake off the GUI thread onto a real bounded
`frame_queue`~~ — ✅ **DONE**, see item 20 in the Done table. Intake now runs on the camera
thread against a bounded *blocking* queue, with `overload_detected` at 80 % full.

---

#### ~~A4 · float32 vs float64 in the corrected numerator~~ — ✅ DECIDED 2026-10-20: keep float32 *(was worklist task 22)*

CLAUDE.md requires `np.float64` for all SCOS intermediates; `processor.py` computes the local
variance and the corrected numerator `var_im − G·mean − spVar − dark_var − 1/12` in float32.
Review A is explicit that this is **not** a demonstrated accuracy bug — the offline MATLAB
tests pass at 0.6–1.2 % — but it is a written rule the code breaks, and the subtraction of
nearly equal numbers is exactly where float32 would hurt, at small κ² (high flow).

**Decision (user, 2026-10-20): keep float32** for the per-frame κ²; no question to Vika. The
calibration (dark and bright) is already computed in float64; CLAUDE.md §Code Style records
the exception. **Measured that day** (scratch script, not committed): a float64 copy of the
same formula against `processor.py` on `expT5ms_Gain24dB_BL100DU_FR40Hz_005`, frames 1–100,
600-frame dark calibration, `smoothingCoefficients.mat` spVar, `totMask` — κ²_raw and κ²_corr
differ by 5 × 10⁻⁸ relative on average (at most 1.4 × 10⁻⁷); the error against MATLAB's
`LocalStd7x7_corr.mat` is 1.055 % with either. float64 is ≈ 2× slower (whole 1216 × 1936
frame on the dev PC: 72.6 vs 141.5 ms). MATLAB itself uses double throughout (frames read
with `double()`, `SCOSvsTime_WithNoiseSubtraction_Ver2.m:380,407,409`, `baseFunc/ReadRecord.m:84`;
no `single()` in the repo). κ²_corr there ≈ 0.008; at a smaller κ² the gap grows, from
5 × 10⁻⁸. No small-κ² test was written — not asked for.

---

#### A5 · Vika's run on saved frames: raw κ² agrees, corrected κ² is 8 % off — find out why *(reported by the user 2026-10-29)*

**What came back.** Vika ran her MATLAB script (`SCOSvsTime_WithNoiseSubtraction_Ver2`) on the frames
the app saved in session `TestKsenia\scos_20261022_165057` (the rig check of Save Frames, `rig_checklist.md`
R8d; PRO camera SN 40513592, cut 500 × 495, 8 ms, 8 dB, 20 Hz, BL 100, 1 min, light weak that day,
⟨I⟩ ≈ 33 DU). Against the app's results:
- **raw κ²: 0.01 % apart** — the frames, the mask and the mean intensity agree;
- **corrected κ² ("raw contrast after cleaning"): 8 % apart** — too much to leave unexplained. **Needed
  before the v0 tag (E5).**
- One more frame was saved than processed: **1301 measurement frames saved, 1300 results** (known and
  expected — the last frame was still inside the pipeline at Stop SCOS, "tail 1", Done item 62). Her
  script reads all 1301. One frame in 1300 moves a mean by about 0.1 % at most, so it cannot explain
  8 % — but check that her per-frame comparison pairs frames by index (`frame_times.csv`), not shifted
  by one.

**Why raw agrees and corrected does not — where to look.** Raw κ² = var / ⟨I⟩². Corrected κ² subtracts
noise terms from the variance first: G·⟨I⟩ (shot noise), `spVar` (the fixed speckle pattern), the dark
variance, 1/12 (quantization). Raw agreeing to 0.01 % rules out the frames, mask and ⟨I⟩; the gap is in
one of the subtracted terms. **Weak light makes it large:** what is left after the subtraction is a
small difference of large numbers, and at ⟨I⟩ ≈ 33 DU a small absolute difference in one term is a
big relative change in κ²_corr. **This session's numbers** (open question 20): raw κ² ≈ 0.033, shot noise
G/⟨I⟩ ≈ 0.028 (G = 0.931 for SN 40513592), corrected κ² ≈ 0.0017 — only ≈ 5 % of raw. So 8 % of κ²_corr
≈ 1.4 × 10⁻⁴ ≈ **0.4 % of raw κ²**: any one noise term off by that much explains the whole gap. Example:
G = 0.956 (the rig camera SN 40075248's value) instead of 0.931 would move the shot-noise term by
≈ 7 × 10⁻⁴ — about five times the gap — so G is worth checking first, it is the cheapest.
Candidates, most likely first:
1. **`spVar` source — expected to differ.** Her script takes `spVar` from the first 600 **measurement**
   frames; the app takes it from the 600 **bright-calibration** frames (R8d's "Expect" column already
   says "close but not identical"). Also open question 17 (the leftover temporal noise in `spVar`
   depends on the number of frames averaged).
2. **The dark variance** — same 600 dark frames (`_dark` folder), but check how each side builds it
   (per pixel vs. one number; subtracting the black level or the dark mean).
3. **G** — both should read `CamerasMeasuredGain.csv` for SN 40513592 Mono12 at 8 dB (0.931); check the
   value her run actually used (16-bit TIFFs are read as Mono12; the SN is in the file names).
4. The 1/12 term, and any term one side applies and the other does not.

**Steps.**
1. Get from Vika her outputs for this run: the κ²_corr per frame (or the `.mat` it saves), and the
   `spVar`, dark variance and G she used. Ask how she computed "8 %" (mean over the session? per frame?).
2. `tools/compare_with_matlab.py "<session folder>"` on her output.
3. Term by term: the ROI mean of each subtracted term on both sides, in DU², next to var − G·⟨I⟩ — the
   term whose difference is ≈ 8 % of κ²_corr · ⟨I⟩² is the cause.
4. If it is `spVar` (candidate 1): rerun the app's math with her `spVar` (from the first 600 measurement
   frames) — it should then agree; then the question for Vika is which source is right, not a bug.

---

### Tier E — v0 Release Requirements

These are the concrete items needed before tagging version 0. They come from the
supervisor's requirements (2026-06-04 PDF). Do after Tier A, before architecture cleanup.

---

#### ~~E1 · "Pulsation lower level" normalization — short vs long recording~~ — ✅ DONE (see item 23 in Done table)

**What the MATLAB reference does** (from `SCOSvsTime_WithNoiseSubtraction_Ver2.m` line 505):
```matlab
if timeVec(end) > 120   % long recording
    rBFi = BFi / mean(BFi(1 : round(norm_seconds * frameRate)));
else                    % short recording
    rBFi = BFi / prctile(BFi(1 : round(norm_seconds * frameRate)), 5);
end
```

**What is currently done:** both modes compute `mean` (the "pulsation lower level" option
logs a warning and falls back to mean at `gui/main_window.py:1124`).

**What to implement:**
- The normalization constant cannot be chosen until the recording ends (because "short vs
  long" depends on the final duration). Keep the live plot using `mean` during the session.
- At `FINISHED` (Stop SCOS or auto-stop): check actual `elapsed_measuring`.
  - If > 120 s → normalization constant stays as-is (mean of first `norm_seconds`)
  - If ≤ 120 s → recompute constant as `np.percentile(bfi_norm_buffer_values, 5)`, re-scale
    the already-plotted data, and save the corrected `rBFi` to HDF5.
- The `norm_seconds` spinbox already exists in the GUI and controls the window length.
- Also: if total time > 120 s, **convert the plot x-axis to minutes** (not seconds).

---

#### E2 · Correct HDF5 file structure per protocol

**Required output files** (from supervisor's PDF):

| File | Contents |
|---|---|
| `rBFi_resultsAndParameters.h5` | `startTime`, `timeVec`, `rBFi`, `Intensity`, `Params` (group), plus `k2_raw`, `k2_corr`, `bfi` and a `metadata` group |
| `rBfi_fig.png` | rBFi above ⟨I⟩ plus a parameters box, drawn from the results file at session end (`core/results_figure.py`, U5 / Done item 45; before that `PlotWidget.save_png()`, rBFi only) |
| `Calibration.h5` | One file, two groups: `dark` and `bright` |

*Filenames above are what the code actually writes. The results file was `rBfi_results.h5` (lower-case f, her spelling in `docs/session_tab`, verified 2026-10-04) until 2026-10-11, when it took the protocol's name `rBFi_resultsAndParameters.h5` (U9, Done item 48); the figure keeps `rBfi_fig.png`. The supervisor replaced the two separate calibration files with one `Calibration.h5` on 2026-09-23; the original PDF's `DarkCalibration.h5` / `BrightCalibration.h5` no longer apply.*

**DONE 2026-09-23** — see merged_worklist task 9 for the full note. The schema now matches
the supervisor's spec and her answers of 2026-09-23:

- `rBfi_results.h5` (her spelling) with `startTime` (fixed-length ASCII, `23-Sep-2026
  15:41:47`, so MATLAB's `datetime()` reads a char row and not a cell array), `timeVec`,
  `rBFi`, `Intensity`, and a `Params` group of exactly her ten fields — including
  `normalizationConstant`, `normalizationMethod`, `normalizationWindowSec` and `gitCommit`.
  `satCapacity` is excluded by instruction. `k2_raw`, `k2_corr` and raw `bfi` are kept
  alongside; camera/G provenance sits in a separate `metadata` group.
- `rBFi` is computed and written **once at close**, in `_finish_session()`, from raw BFi
  buffered on disk during the session.
- **One calibration file, not two:** `Calibration.h5` with `dark` and `bright` groups. This
  overrides the two-file table above — the supervisor changed it on 2026-09-23. The old
  `dark_cal_*.mat` / `bright_cal_*.mat` writes and the embedded `calibration` group are gone.
- `h5_replay.py` reads both the old and new dataset names, so existing recordings still play.

Still open for Tier E: **only the v0 tag (E5)**. The normalization method (E1, item 23), the
laser popup (E3, item 28) and the figure file (E4, item 27) have all landed since this
paragraph was written.

---

#### ~~E3 · End-of-session popup + laser-off intensity check~~ — ✅ DONE (see item 28 in Done table)

**What the supervisor requires:**
1. When measurement ends (Stop SCOS button or auto-stop by duration), show a pop-up:
   *"Measurement has ended. Please turn off the laser."* with an OK button.
2. After the user clicks OK, capture one frame and check that the mean intensity over the
   ROI has dropped by ≥ 90 % compared to the last known measurement intensity.
   - If yes: proceed to flush and save normally.
   - If no: show a warning: *"Laser may still be on — mean intensity did not drop by 90 %
     (measured: X DU, expected: < Y DU). Continue anyway?"*

**Where to add it:** in `_toggle_scos(False)` / `_set_state(FINISHED)` in
`gui/main_window.py`. The last `mean_i` value is already available from `_on_scos_result`.

---

#### ~~E4 · Save plot figure at end of session~~ — ✅ DONE (see item 27 in Done table)

At `FINISHED`, export the BFI time-series plot to a PNG file in the same output folder:
```python
# As implemented: PlotWidget.save_png(), rendered from the plot item at a fixed
# 1600 px, called after _finalize_normalization() so the curve matches the saved
# constant. Writes <session folder>/rBfi_fig.png.
# Replaced 2026-10-10 (U5, Done item 45): core/results_figure.py draws it from
# the results file — rBFi, <I> and a parameters box, 1600 x 900 px.
```
Show the saved path in the status bar.

---

#### E5 · Git tag v0

**Decided 2026-10-20 (user):** the tag waits until **Vika has checked the math on saved
frames**. If those are frames saved *by this app*, raw-frame saving (F1) must come first —
done 2026-10-21 (Done item 58); rig check passed 2026-10-22 (`rig_checklist.md` R8a–c,
session `scos_20261022_165057`). **Her check came back (2026-10-29):** raw κ² 0.01 % apart,
corrected κ² 8 % apart — **A5 must explain the 8 % before the tag.**

After E1–E4 are done and all tests pass, create an annotated git tag:
```
git tag -a v0 -m "Version 0: complete real-time SCOS with calibration, normalization, HDF5 save"
```

---

### Tier B — Operator Safety

These protect against data loss and let the operator react before a session is ruined.
Implement after Tier A and E are done.

---

#### B1 · Queue fill indicator + overload dialog

**Queue fill bar:** Add a small progress bar (0–100 %) to the status bar showing
`frame_queue.qsize() / frame_queue.maxsize`. Update it every second via the existing
`QTimer`. Add a dropped-frame counter label next to it.

**Overload dialog:** When queue fill ≥ 80 %, pause capture and show a non-blocking dialog
with 4 options (least-disruptive first):
1. Reduce ROI radius
2. Reduce target FPS
3. Switch to save-only (write raw frames, skip κ² — process later)
4. Switch to process-only (skip disk, keep computing)

5–10 s timeout → default to save-only. On timeout, log the decision and resume.

---

#### ~~B2 · Disk-space check~~ — ✅ DONE 2026-10-21 for saved frames (see item 58 in Done table)

*Done with F1:* `core/disk_space.py` — before Start SCOS with Save Frames ticked, free space ≥ estimate × 1.2 + 5 GB ("unlimited" = 4 h); a measurement stops cleanly below 2 GB, checked every 30 s. Without Save Frames there is no check (a session writes < 50 MB). The limits are the proposed ones, never confirmed by Vika (open question 14), kept as constants.


Before starting a recording, call `shutil.disk_usage(output_folder)` and refuse to start
if free space < some threshold (e.g. 5 GB). During a session, check every few minutes and
stop gracefully if space drops below 1 GB. Show remaining disk space in the status bar.

**2026-10-07: Vika likes the check before recording** (open question 14). She asked for the
space needed by bit depth, recording length and Save Frames. That table, and proposed
limits (start: estimate × 1.2 + 5 GB; stop cleanly below 2 GB, checked every ~30 s), are in
`docs/open_questions.md` question 14, waiting for her to confirm the limits. In short: without
frames a session is < 30 MB at any length; with every frame saved at 700 × 700, 20 Hz,
Mono10/12, it is ~1.2 GB per minute and ~70 GB per hour (half that at Mono8). The estimate
must be uncompressed: speckle barely compresses, and a check must never underestimate.

---

#### ~~B3 · Persist GUI settings between launches~~ — ✅ DONE (see item 30 in Done table)

> Implemented with one deliberate change to the plan below: settings are written to a
> gitignored `scos_config.local.json`, **not** back into `scos_config.json`, which is
> tracked by git (user's decision, 2026-10-04).

Nothing the operator sets in the GUI survives closing the window. `_load_config()`
(`gui/main_window.py`) reads `scos_config.json` into the widgets at startup, but no counterpart
ever writes it back — grep for `json.dump` finds nothing. So every launch resets exposure, gain,
frame rate, window size, dark/bright frame counts, normalization seconds and measurement
duration to whatever is hardcoded in the widget constructors or listed in the config file.

Found on 2026-09-26: the operator set `Dark Frames` to 60, relaunched the app, and the run used
600 again. Workaround applied that day — `n_dark_frames` / `n_bright_frames` were added to
`scos_config.json` by hand (the loader already knew those keys; only the file lacked them).
That is a patch for two fields, not a fix.

**Implement:** a `_save_config()` that writes the same key set `_load_config()` reads, called
from `closeEvent()` before the threads are stopped. Keep the file human-editable (indent=4,
stable key order) — it is currently edited by hand and should stay that way. Write to a temp
file and rename, so a crash mid-write cannot leave an unparsable config and brick the next
launch. Do not save a setting that was forced by the mode rather than chosen by the operator
(e.g. `external_trigger` is switched off and disabled in `--mock-folder` playback).

**Also remember the last results folder.** `_output_root` is neither a widget nor a config
key today: it is asked for once per window launch via `QFileDialog` in `_start_dark_cal()` and
lost on close, so the operator re-navigates to the same place at every launch. Save the chosen
path as `output_root` in the config and pass it as the dialog's starting directory next time.
**Keep showing the dialog** — do not silently reuse the path. Writing a session into the
previous subject's folder is far worse than one extra click, and the operator may be working
with a different subject or a different drive. This one differs from the settings above in that
`_load_config()` does not read it either, so both halves need adding.

**Note — not the same bug as the "600 frames" auto-load.** In `--mock-folder` mode,
`_auto_load_folder_calibration()` streams *every* dark TIFF in the recording folder and ignores
the `Dark Frames` spinbox entirely. That path does not exist for a real camera (it is gated on
`hasattr(camera, "get_calibration_mat")`, which only `FolderMockCamera` has), so it needs no
fix — but the two are easy to confuse when reading a bug report.

---

#### B4 · Automatic camera reconnect *(was worklist task 19)*

A GigE dropout during an unattended multi-hour run currently ends the acquisition:
`_on_camera_error` un-checks Start Video and waits for the operator. **Done when:** a simulated
disconnect recovers on its own, into the camera-thread intake path, and the gap is logged
and marked in the data (so `timeVec` shows it rather than hiding it).

#### B5 · An ROI circle with no pixel on the image *(found 2026-10-11, U6 step 6 — not verified in the app)*

In the rehearsal mutation `cut-offset` with `--cut-radius 150` the ROI mask had **no pixels**
(the circle fell outside the frame), and `_on_scos_frame`'s intensity statistics
(`np.percentile` over the masked pixels) raised `IndexError` on **every frame** — 3 673
exceptions in one run. With the real crop code that cannot happen (the crop is built around
the circle). Whether it can in the app as it is: an operator who drags the circle wholly off
the image before Start SCOS might produce the same empty mask — not tried. In a real run an
exception escaping a Qt slot can abort the app. **To do:** try it on `--mock-folder`; if it
reproduces, refuse an empty ROI (Start SCOS and the statistics), with a message.

---

### Tier C — Architecture Cleanup

Refactoring the math and camera layers into clean, testable modules. Do after Tier A and E
are solid — don't refactor code that still has correctness bugs or missing protocol features.

---

#### C1 · `core/scos_math.py` — extract pure math from `processor.py`

Move the κ² formula, gain conversion, `local_mean_and_variance`, and `shrink_mask_for_window`
into a pure-functions module with no camera/GUI imports. This makes unit testing trivial
and isolates the scientific core from infrastructure.

Key functions to extract:
```python
load_gain_from_table(camera_sn, n_bits, gain_db) -> float   # G for measurements: table only
convert_gain(gain_db, bit_depth, sat_capacity) -> float     # synthetic/test sources ONLY
local_mean_and_variance(im, window) -> (mean_im, var_im)
shrink_mask_for_window(mask, window) -> np.ndarray
compute_kappa2(frame, mask, window, mean_dark, var_dark_filtered,
               var_bright_filtered, G_due) -> (kappa2_raw, kappa2_fixed, mean_intensity)
```

Add `test_scos_math.py` with offline-reference test against the MATLAB output.

---

#### C2 · `core/frame_source.py` ABC

Define a `FrameSource` abstract base class with `open()`, `get_frame(timeout_s)`, `close()`.
Make `CameraThread`, `MockCameraThread`, and `FolderMockCamera` implement it so they are
interchangeable without duck-typing. Prerequisite for C3.

---

#### C3 · `core/camera_source.py` with `trigger_mode` parameter

A clean `CameraFrameSource` wrapping pypylon, replacing `camera.py` for the refactored
architecture. Must support `trigger_mode='off'|'line2'`. Document: with
`trigger_mode='line2'`, `frame_rate_hz` is a target — actual rate is whatever the Arduino
delivers on Line2.

---

### Tier D — Tooling & Docs

These can land in any order and don't block correctness or release work.

---

#### D1 · `setDownsampling` + `setClipToView` in `gui/plot_widget.py`

For recordings longer than ~100 k points, pyqtgraph will lag without downsampling.
One-line fix when the plot curve is created:
```python
self._curve.setDownsampling(auto=True, mode='peak')
self._curve.setClipToView(True)
```

---

#### D2 · `pyproject.toml` + `requirements.lock`

Consolidate pytest/ruff/mypy config in `pyproject.toml`. Run `pip-compile` to produce
`requirements.lock` for reproducible installs. *(Also covers worklist task 25: `ruff check` and `mypy core/` clean in the pre-commit hook and in CI — explicitly last, accuracy work comes first.)*

---

#### D3 · GitHub Actions CI

Add `.github/workflows/ci.yml` to run `ruff check` + `pytest` on every push and pull
request.

---

#### D4 · `docs/realtime_architecture.md`

A document that answers the open architecture questions from `Implementation_Plan.md §9`
(rBFI normalization policy, disk-space limit policy, save-frames policy). Needs supervisor
input before writing.

---

#### D5 · Verify the grab loop on real hardware — lost frames and the camera clock

Mocks bypass `camera.py`'s `run()` loop entirely, so this needs the real camera. Since
2026-10-07 (Done item 34) that loop takes each frame's **capture time from the camera's own
clock** and **counts lost frames from the camera's frame numbers** (`core/frame_clock.py`).
The fake-camera tests in `tests/test_camera.py` cover the logic, not the real camera.

**Why it changed (found in rig prep 3b, 2026-10-06).** Two gaps, both confirmed in Basler's
pylon API reference for `CGrabResultData`:
- *Lost frames were counted nowhere.* `GetNumberOfSkippedImages()` counts only under the
  `LatestImageOnly` / `LatestImages` strategies and *"does not include the number of images
  lost in case of a buffer underrun in the driver"*. Under `OneByOne`, which `camera.py`
  uses, a frame lost because all 20 buffers were full produced no warning and no count.
  (The old step "no warning at high FPS = the buffer is absorbing them" was therefore
  wrong.) Now: a jump in `BlockID` — the camera's frame number; on GigE it runs 1…65535 and
  wraps, on USB it starts at **0** — is counted as lost frames, and so is a grab that arrived
  incomplete. (First rig run 2026-10-08: the USB camera's first frame 0 was mistaken for "no
  frame numbers" and warned about; fixed, Done item 41.)
- *Timestamps could bunch up.* `t_capture` was the PC time right after `RetrieveResult()`,
  i.e. when a frame **left Pylon's buffer**. After a stall, frames that waited there were
  retrieved in a burst with bunched stamps. Now: the grab result's `TimeStamp` (camera
  ticks at the start of exposure), converted to seconds.

**The safety rule.** The camera clock is used only after it agrees with the PC clock
within 1 % over the first 5 s after Start Video (or after any grab restart), for the
camera's reported tick rate (`GevTimestampTickFrequency`) or, if it reports none, for one
of the two Basler rates (1 GHz, 125 MHz). Until then — and for good if it never agrees, the
camera has no timestamps, or its clock runs backwards — frames get the old PC stamp, and
`app.log` says why. A wrong tick rate would stretch the whole `timeVec`, so it is never
guessed. A measurement starts only after both calibrations, so the check has long finished.

**How to verify at the rig** (part of step 5a):
1. Start Video. Within ~5 s `app.log` must say **"Camera clock accepted — … MHz ticks
   agree with the PC clock"**. "Camera clock not used — …" instead → note the reason; the
   session is still valid (old PC stamps), but tell me.
2. Run a short session. In `rBFi_resultsAndParameters.h5`, `metadata` must have **`time_source =
   "camera"`**, **`frames_lost_camera = 0`**, **`frames_dropped_queue = 0`**, and
   `np.diff(timeVec)` must be ≈ 1/FPS (50 ms at 20 Hz) throughout.
3. Deliberately overload — raise FPS to what the PC cannot sustain (e.g. 150 Hz) — and
   check: the warning **"Camera: N frame(s) lost — Pylon buffers full or transfer
   failed"** in the status bar and `app.log`; during a measurement, the label reads
   **"Dropped: N + M lost at camera"**; the grab loop *blocks* rather than freezing the
   GUI; `overload_detected` appears; and the gaps in `timeVec` are whole multiples of the
   frame period (lost frames leave true-size gaps, no bunching).

**Rig result, 2026-10-08** (a2A1920-160umBAS, SN 40075248, USB; session
`TestVika10082026_20261008_145048`, 1 min, 20 Hz, hardware trigger from the Arduino):
- Step 1 ✅ — "Camera clock accepted — 1000 MHz ticks agree with the PC clock over 5.0 s",
  after every grab restart (four times).
- Step 2 ✅ — `time_source = "camera"`, `frames_lost_camera = 0`, `frames_dropped_queue = 0`;
  1300 frames, every `np.diff(timeVec)` exactly 0.0500 s, no gaps.
- Found: "Camera reports no frame numbers (BlockID 0)" on the first frame — a false alarm,
  USB block IDs start at 0; fixed `dffa06d` (Done item 41). Still to see on the camera:
  `BlockID` reading 0, 1, 2, … (camera was disconnected when tried).
- Step 3 (deliberate overload) **not run yet**.

**Rig result, 2026-10-19** (same camera; benchmark R1f and overload R7, session
`TestKsenia/rig_full_600/Overload1/Overload1_20261019_203127`):
- Steps 1–2 ✅ again in every session that day — camera clock accepted after every grab
  restart, 0 lost, 0 dropped, time steps exactly one frame period.
- Frame numbers ✅ — no "Camera reports no frame numbers" warning in any of the day's four
  `app.log` files (checked 2026-10-20), so the USB-starts-at-0 fix (`dffa06d`) works on the camera.
- **R1f benchmark** (`bench_processor.py --width 1936 --height 1216 --window 7 --bits 12
  --fps 20`, one worker): mean 12.3 ms (p99 16.9), "Max sustainable 81.6 Hz". Its ROI is a
  circle of r = 364 (30 % of the short side); **processing time scales with the ROI's
  bounding box, not the frame**: measured on the rig PC with `SCOSProcessor.process()` on synthetic full frames, r = 243 →
  4.9 ms, r = 364 → 12.0 ms per frame, and a dark frame costs the same as a lit one
  (4.81 vs 4.91 ms, interleaved).
- **Step 3 / R7** — External Trigger off, 1 worker, exposure 2 ms, FPS set 150, full sensor,
  ROI r = 243: the camera delivered **76.46 fps** (its USB limit on the full sensor). One
  "SCOS overload — input queue 16/20 full; camera capture is being throttled" at the start of
  the measurement, then the pipeline kept up: **0 lost, 0 dropped**, 4973 frames in 65 s,
  every step exactly 13.079 ms (no gap, no bunching). **This camera cannot outrun this PC**
  at a normal ROI (≈ 200 frames/s with one worker), so the lost-frame path ("Camera: N
  frame(s) lost") still has not been seen on the real camera; the throttling path has.
  `check_session.py` printed 5 NOT OK — all from comparing with the set 150 Hz (see Pending
  decisions at the top).
- The light in that session came in only ~20 % of the frames — the Arduino kept flashing
  the laser with the trigger off (F3 below).

---

#### ~~D6 · Start SCOS destroys the auto-loaded calibration in `--mock-folder`~~ — ✅ DONE (see item 24 in Done table)

`_auto_load_folder_calibration()` loads a correct calibration when a recording folder is
replayed: `dark_mean`/`dark_var` streamed from the `_dark` folder, `spVar` from
`smoothingCoefficients.mat`, the ROI mask from `Mask.mat`, and `processor.scale = 64`. Start
SCOS then runs the live dark and bright calibrations on top and overwrites all of it — and in
playback there is no laser to switch off, so those 60 "dark" frames are the laser-on recording.
Two independent faults follow: `dark_var` carries the live signal's variance, and `dark_mean`
is in raw units while `process()` divides the frame by 64.

Result: corrected κ² is negative in every frame and the run aborts (see Done item 22). A
mock-folder rehearsal therefore cannot currently reach MEASURING, which is exactly what that
mode exists to rehearse.

**Measured 2026-09-27** — this is not a limit of the mode. Feeding the same main TIFFs through
a processor holding only the auto-loaded calibration gives `dark_mean` = 99.3 DU (the BL100DU
baseline, i.e. genuinely dark), `dark_var` = 5.6, and κ²_corr ≈ 0.0104 against MATLAB's
0.0105 — a 1.3 % match. The calibration is good; Start SCOS simply throws it away.
*(Note 2026-10-08: `LocalStd7x7_corr.mat` averages 0.00838, not 0.0105; where 0.0105 came
from is not traced — single frames reach it, the mean does not. See Done item 40.)*

**Two ways to fix, pick one:**

1. *Skip the live calibration in playback* and keep the auto-loaded arrays. Cheapest, but
   `Calibration.h5` is written inside `_finish_dark_cal` / `_finish_bright_cal`, so skipping
   them leaves the session folder without the very file whose layout the rehearsal is meant to
   prove. The auto-loaded arrays would have to be routed through `write_calibration()` too.
2. *Serve dark frames from the dark folder.* Have `FolderMockCamera` play the `_dark` TIFFs
   while the state is `DARK_CAL`. Every prompt, collector, folder dialog and file write then
   runs exactly as on real hardware and gets frames that really are dark — a faithful
   rehearsal instead of a bypassed one. More work, and bright calibration still has no clean
   source, since the main recording was made with a subject in place. *(Corrected 2026-10-08:
   it is clean — the bright calibration is taken with the subject in place.)*

---

#### D7 · Write down the worker-pool decision *(was worklist task 7)*

Record whether the multi-worker pool stays, with the timing numbers behind it — belongs in
`docs/realtime_architecture.md` (D4). Recommendation already made in the reviews: keep the
pool **with** the in-flight cap (`_inflight_sem`); going single-thread would remove the
`spn_workers` operator control. Needs the lab-PC benchmark from the rig session.

---

#### ~~D8 · Faster dark-calibration collector~~ — ✅ DONE 2026-10-11 (see item 51 in Done table) *(found 2026-10-06, rig prep 3a)*

The calibration collectors run on the GUI thread, one frame at a time. When they are slower
than the camera, the frames they have not reached yet queue up in Qt's event queue and are
dropped by `_flush_stale_frames()` at the next prompt. Measured with `tools/rehearsal.py` on
the 2.4-Mpx, 40 Hz playback, 600 frames each:

| | per frame | 600 frames | frames queued, then flushed |
|---|---|---|---|
| dark (`DarkCalCollector`, mean **and** variance) | 113 ms in the app, 89 ms collector alone | 68 s | 1555 (≈ 7 GB of uint16 frames held in memory) |
| bright (`BrightCalCollector`, mean only) | 67 ms in the app, 34 ms collector alone | 40 s | — |

The frames that *are* used are the right ones — the queue is first-in first-out, so the
collector takes the first 600 captured after OK, consecutive, as MATLAB does. The cost is
time and memory, not accuracy.

**Not** "skip frames as they arrive": it would keep memory flat but not shorten the
calibration (the arithmetic is the limit: 600 × 113 ms either way), and it would spread the
600 frames over a minute instead of 15 s, letting slow sensor drift into `dark_var`.

**Do instead:** rewrite `DarkCalCollector.add_frame()`'s Welford update in place with
preallocated buffers — it currently allocates four temporary 19 MB arrays per frame. Same
arithmetic, so results must not change. **Done when:** per-frame time is measured before
and after on 1216 × 1936 frames, `tests/test_dark_cal_offline.py` still passes, and the
dark arrays match the old collector's on the same frames.

~~**Probably unnecessary on the rig:** 700 × 700 at 20 Hz scales to ≈ 24 ms of work per frame
against a 50 ms budget. Decide after the rig check in step 5a.~~ **Rig, 2026-10-08:** the camera
sends 1216 × 1936 (not 700 × 700), and 600 dark frames took 30.2 s at 20 Hz — the collector
kept up, so at 20 Hz D8 is not needed. **It is needed for U11** (dark calibration at the
camera's maximum frame rate, Vika's request): there the collector, not the camera, sets the
speed. Moving the collectors off the
GUI thread altogether is the bigger alternative and a threading change — not before v0. When this lands, also update the `MainWindow._flush_stale_frames()` docstring, which still quotes only the 60–130-frame backlog measured with 60-frame calibrations.

---

#### D9 · Intermittent fatal crash of the fast test suite *(found 2026-10-11)* — **low priority, test-only** (user, 2026-10-20)

**Decided 2026-10-20: low priority.** It happens only in `pytest`, never in the app (real
camera or playback): no app code connects a lambda to a signal (searched `gui/`, `core/`, the
camera files) — the app connects bound methods of the main window, which Qt disconnects by
itself when the window goes. Cost is a re-run when the pre-commit hook trips (≈ 4 times in two
weeks). Fix only if it starts getting in the way (the lead and the fix are below).

`python -m pytest tests/ -m "not slow"` twice ended with a Python **fatal-error report**
instead of a test summary — only its last line was seen: `File "<frozen runpy>", line 198
in _run_module_as_main`. That line closes the stack dump pytest's `faulthandler` prints
when the process dies in C code (an access violation), which here most likely means Qt or
pyqtgraph while widgets are being torn down. It is **not** a failing test.

What is known:
- **Twice on 2026-10-11**: once during U9 (before any crop code existed — so not U6's), and
  once right after a slow-suite run in the same command.
- **Not reproducible on demand:** 8 consecutive runs afterwards all passed (557 passed).
- **2026-10-12, probably again:** the pre-commit hook blocked the U11 step 4 commit ("tests
  failed"); only the hook's last line was printed, so whether it was this crash or a failing
  test is not known. Two runs straight after passed (652/652), and so did the hook on the
  retry. Next time, keep the hook's whole output.
- **Risk:** the pre-commit hook runs this suite, so a crash can block a commit; re-running
  gets past it. No effect on the app or on measurements has been seen.

**Not the same as** the exit-127 crash found the same day (no report at all, reproducible: `test_folder_camera.py` run before `test_crop.py` left a bare `QCoreApplication` for a widget test) — that one was fixed in `tests/conftest.py`. D9 prints a fatal-error report and is not reproducible.

**Probably related (U6 step 5, 2026-10-11):** while `tests/test_cut_image.py` was being
written, the suite sometimes ended — after "595 passed", exit code 0 — with 2–4
`AttributeError: 'LabelItem' object has no attribute '_sizeHint'` tracebacks from
pyqtgraph at interpreter shutdown: in every run of one version of that file, in none of
another (the current one: 0 in 2 runs). Each class of the file on its own never
triggered it. Harmless as seen, but it is Qt/pyqtgraph objects destroyed in the wrong
order at exit — the same family as the crash above. **Tried and reverted:** deleting all
leftover windows in a `pytest_sessionfinish` hook in `conftest.py` turned it into a fatal
crash (exit code 3) on 3 of 3 runs — don't repeat it.

**2026-10-19, a different symptom:** one fast-suite run (with the settings fix, Done item
57) ended `1 failed, 706 passed` — **a failing test, not the crash**:
`tests/test_calibration_exits.py::TestStoppingDuringCalibration::test_stop_scos_during_bright_cal`
(it checks that a run stopped during the bright calibration renames its folder to
`<name>_cancelled`). The error text was not kept. Then 5 full runs passed (707) and 25
runs of that file alone passed. Unconfirmed guess: the folder rename lost a race with
another program holding the file (Windows). Keep the output next time.

**2026-10-20 — the crash itself, with the whole dump for the first time** (pre-commit hook
of a docs-only commit; about 92 % through the suite): `Windows fatal exception: access
violation`, then `Segmentation fault`. The crashing thread was the main thread inside
`tests/test_window_fits_screen.py:47` `_settle()` → `_app.processEvents()`, called from its
`win` fixture (line 58) — and the frame it was running was **`<lambda>` in
`tests/test_playback_calibration.py`**, a file that had finished earlier. The only lambda
there connected to a signal is `test_emitted_frames_follow_the_source` (line 124):
`cam.frame_ready.connect(lambda f, t: frames.append(...))` on a `FolderMockCamera` running
at 200 fps; the test calls `cam.stop()` and returns without disconnecting or draining the
event queue. **Lead (not yet proven):** queued `frame_ready` deliveries emitted before
`stop()` stay in Qt's event queue after that test (and its camera) are gone, and the next
`processEvents()` anywhere in the suite delivers one to a dead connection. Two pipeline
threads (`core/pipeline.py` `_emit_loop`, `run`) were also waiting at the time — idle, not
in the crash. Next: in that test, disconnect the lambda and drain events after `stop()`
(or connect a slot on an object the test owns), then run the suite ≥ 30 times.

**2026-10-21, once more — output not kept:** one full fast run, before the Save Frames commit,
ended `1 failed, 772 passed`; which test is not known (the output was not saved). The 7 runs
after it all passed (5 by hand, 2 in the pre-commit hook). Two new timing-based tests in
`tests/test_frame_writer.py` used a 20 ms sleep to let the writer thread take a frame; they now
wait for it explicitly — a guess at the cause, not a proven one.

**2026-10-29, again in the pre-commit hook — output not kept:** the commit of the steady-leak
warning (Done item 64) was blocked by `Segmentation fault` (exit from the hook's pytest; only the
last lines were seen: `<frozen runpy>` frames). The retry seconds later passed (868 passed,
86 s) and the commit went in (`a41e29a`). The full suite had passed shortly before on almost
the same code (867, before a two-line guard and its test were added). The hook itself shows only the tail — the lesson repeats:
keep the whole output on the first try (`git commit … > commit.log 2>&1`).

**2026-10-29, second time that day — the first full dump** (plain `pytest -m "not slow"`, while
building the desktop icon; saved as [`crash_logs/2026-10-29_fast_suite_access_violation.log`](crash_logs/2026-10-29_fast_suite_access_violation.log)).
`Windows fatal exception: access violation` at 98 %, in the **setup of `tests/test_window_fits_screen.py`'s
`win` fixture** (`_settle()` → `processEvents()`). What that `processEvents()` was running is a
**`<lambda>` from `tests/test_playback_calibration.py`** (line `???`) — a test long finished. The only
lambda there that Qt can call is `cam.frame_ready.connect(lambda f, t: frames.append(…))` in
`test_emitted_frames_follow_the_source` (line 124). **Likely, not proven:** the playback camera is
stopped and dropped at the end of the test, but frames it emitted through the **queued** connection are
still waiting in Qt's event queue, and are delivered to a lambda whose camera (and PyQt's proxy for the
lambda) is gone.
Not a freed frame: `FolderMockCamera` emits a fresh `tifffile.imread` array each time. Also alive at the
moment of the crash: **two threads of an earlier test's `RealtimePipeline`** (`core/pipeline.py` `_emit_loop`
and a worker's `run`, both waiting) — a pipeline some test never shut down. **Lead for a fix (not tried):**
in that test, disconnect `frame_ready` and drain the queue (`processEvents()` until empty) after
`cam.stop()`, or give the lambda a context object that lives as long as the camera; and find which test
leaves a pipeline running. The rerun minutes later passed.

**2026-10-31, in the pre-commit hook (a docs-only commit):** the **same dump, line for line** — access
violation in `test_window_fits_screen.py`'s `_settle()`, running the `<lambda>` from
`test_playback_calibration.py`, with the same two leaked `RealtimePipeline` threads. Two dumps out of
two point at that lambda, so the lead above is now the first thing to try.

**Next time it happens:** save the *whole* output (`… > crash.log 2>&1`) — the dump names
the test that was running and the C-level frame — then look at that test's window/thread
teardown. Running with `-v` also shows which test had started last.

**Done when:** the cause is found and fixed, or ≥ 30 consecutive runs pass after a change
that plausibly fixes it.

#### ~~D10 · Short calibrations can start the measurement before the camera clock is accepted~~ — ✅ DONE 2026-10-14 (see item 53 in Done table) *(found 2026-10-12, U11 review)*

`core/frame_clock.py` uses the camera's own timestamps only after checking them against the
PC clock for 5 s after every restart of grabbing. The dark calibration's trigger switch is
such a restart (it is made whatever the trigger setting), and with the trigger On so is
switching it back after the dark calibration. If the measurement starts less than 5 s after
the last restart, its first frames carry PC stamps and the rest camera stamps. Where they
meet, `timeVec` steps by a few milliseconds: the camera clock is aligned to the
least-delayed PC readings. The results file's `time_source` then says `camera` for a
measurement that began on the PC clock.

No restart can happen during a measurement through the panel: every camera setting is
locked from Start SCOS (`_set_params_enabled`), Cut/Full Image too. (The **V** key was the
exception until it was removed — Done item 54.)

| Calibration frames (N1 + N2) | Trigger | Measurement starts after the last restart | |
|---|---|---|---|
| 600 + 600 (default, protocol) | either | ≥ 30 s (the bright calibration alone) | fine |
| 60 + 60 | Off | dark (≈ 0.5–1 s with U11) + **the operator's OK on "Please turn on the laser"** + bright 3 s | inside the check only if OK is clicked within ≈ 1–1.5 s |
| 60 + 60 | On | **the OK on the laser prompt** + bright 3 s | inside the check only if OK is clicked within ≈ 2 s |

*Corrected 2026-10-14:* the first version of this table (U11 review) left out the laser
prompt — its numbers came from headless rehearsals, where the prompt is answered at once.
On the rig the operator switches the laser on first, so a fast enough click is unlikely
but possible; in rehearsals and tests it is the rule.

**Decided 2026-10-14 (user): option (b)** — the measurement keeps no frame until the check
has ended (accepted or given up), so `timeVec` comes from one clock from its first point and
`time_source` is true. Rejected: (a) leave it documented — a results file could still
misdescribe itself; (c) a minimum calibration time — longer calibrations even when the
camera was not restarted, and only indirectly tied to the cause.

---

#### ~~D11 · Start the app from a desktop icon~~ — ✅ DONE 2026-10-29 (see item 65 in Done table); rig check open (`rig_checklist.md` R11)

---

#### D12 · Stretch the graph in the X direction only, to look at one period of time *(user, 2026-10-29)*

The user wants the operator to stretch the rBFi graph **along the time axis only**, to look
closely at one stretch of the recording (for example a few heartbeats). Today
(`gui/plot_widget.py`) the graph has pyqtgraph's default mouse handling: the wheel and
drag zoom and pan **both** axes, and `_refresh()` sets the Y range again every second, so a
Y zoom is undone at once while an X zoom is kept, and the newest data runs off the right
edge. Not designed yet. To decide:
- Allow mouse zoom/pan on X only (`setMouseEnabled(x=True, y=False)`); Y keeps its automatic
  range — over the whole recording, or over the part shown?
- How to go back to "follow the live data": a button (the existing **Reset** button clears
  the data — it must not be mistaken for that), double-click, or pyqtgraph's small "A" button.
- Should it work during a measurement, after Stop SCOS, or both?
- Display only — it must not change what is recorded or saved (`rBfi_fig.png` is drawn from
  the results file, not from this widget). Related: F5 (a reopenable interactive figure
  after the session), D1 (downsampling for long recordings).
- Show a picture of the controls before building (user's habit: minimal version first).

---

### Future Phases (post-v0)

These are the next development phases after v0 is tagged.

---

#### ~~F1 · Save raw frames option~~ — ✅ DONE 2026-10-21 (see item 58 in Done table); rig check ✅ 2026-10-22 (`rig_checklist.md` R8a–c); Vika's check open (R8d)

**2026-10-20: started — plan in [`Plan_Frame_Saving.md`](Plan_Frame_Saving.md)** (with B2 and
F4; format measured: uncompressed TIFF; decisions 1–4 asked). Needed before Vika's check of
the math on saved frames, which the v0 tag waits for.

Add an option to save every raw camera frame to HDF5 during a session. One frame at 700×700
uint16 ≈ 1 MB. At 40 Hz for 30 min ≈ 72 GB — so this requires disk-space check (B2) first.
**Already wired, 2026-10-04 audit:** `MainWindow._on_scos_frame()` calls
`self._recorder.append_frame(frame)` whenever `chk_save_frames` is ticked
(`gui/main_window.py:1818`). What is actually left here is not the wiring:

- the write is **synchronous on the GUI thread**, gzip included — that is worklist task 14;
- there is no disk-space guard in front of it (B2);
- no decision yet on every frame vs every K-th (open question 13).

**The checkbox is disabled (greyed out, still visible — the protocol lists it) since
2026-10-04**, so none of this can bite before F1. Re-enabling it is part of F1, together
with B2 and F4; `tests/test_save_frames_disabled.py` must be updated then.

**Decided by Vika 2026-10-07** (open questions 12 and 13): **every frame**, saved as
**one file per frame** in a `Frames` folder inside the session folder. Not the single
growing `frames` dataset that `append_frame` writes today, which has to be replaced. The
purpose is to rerun the algorithm on saved frames for research and debugging. Hours-long
recordings are not meant to be saved this way, but the option must exist. The file format
is to be **chosen by measurement**: write speed at 20 Hz on the lab PC, size on disk, read
speed back into the pipeline. Candidates: uncompressed TIFF (what Pylon Viewer writes and
`--mock-folder` already reads), TIFF with lossless compression, `.npy`. A 4 h run is
~288 000 files, so think about subfolders.

---

#### F2 · Long sessions (> 2 hours)

Handle recordings longer than 2 hours without memory growth or GUI lag. Requires:
- Pre-allocated NumPy arrays for results (extend in chunks of 10 000) instead of Python lists
- Periodic HDF5 flush every M minutes (already in `HDF5Recorder`)
- Queue fill indicator (B1) to detect and handle sustained overload
- `setDownsampling` in plot widget (D1) to keep rendering fast at 200 k+ points

---

#### F3 · Automatic laser control via Arduino

Instead of pop-up dialogs asking the user to turn the laser on/off, control the laser
directly from the app via the existing Arduino serial connection. The Arduino already
controls the camera trigger; extend it to also toggle the laser enable pin (currently
pin 13 per recent hardware change). Sequence: dark cal → signal LOW → wait → bright cal →
signal HIGH → wait → measure.

**Found on the rig, 2026-10-19 — the Arduino flashes the laser, also with External Trigger
off.** The uploaded sketch (`arduino_uploader.py`) sets pin 7 (camera trigger, Line2) and
pin 13 (laser enable) HIGH together for the exposure time and LOW for the rest of the
period — so the laser is not continuously on: it flashes, 8 ms every 50 ms with the lab
settings. The Arduino runs that loop for as long as it has power, whatever the app does.
Unticking External Trigger only makes the **camera** ignore the pulses; the laser keeps
flashing. A free-running camera then catches light only when its exposure overlaps a flash:
with a 2 ms exposure at 76.5 fps, (8 + 2) / 50 = **20 % lit frames** — measured 19.8 %
(987 of 4973 frames, lit ones ≈ 24 DU, the rest ≈ 0 DU, mostly 52 ms apart) in the R7
session (D5). Consequences:
- **A session with External Trigger off is not a valid measurement** while the Arduino runs
  its sketch — the bright calibration and the measurement mix lit and dark frames.
- The dark calibration is unaffected (the laser is switched off at the prompt, and the app
  restores the trigger after it).
- With the trigger on, every exposure starts with a flash (same Arduino loop), so every
  frame is lit — the normal case.
Open: should the app warn when External Trigger is unticked while an Arduino is connected?
Part of F3's design.

**Found on the rig, 2026-10-22 — a stale Arduino program goes unnoticed (remind the user).**
The app uploads the sketch only when External Trigger is ticked by hand (or exposure / frame
rate change while it is ticked). Opened with the tick already saved, it uploads nothing, so the
Arduino runs whatever was last put on it — that day someone else's program: frames came every
**100 ms** instead of 50 and ⟨I⟩ was ≈ 4× lower, while the results file still said
`frameRate` 20 Hz (and Save Frames would have named the folder `FR20Hz`). Untick + tick fixed
it (session `saving_frames_with_ROI_20261022_163015`: 50.01 ms steps). Options: upload at launch
when the tick is restored, and/or warn when the measured time between frames does not match the
set frame rate. **Decided 2026-10-25: both — done, Done item 60.**

**Found on the rig, 2026-10-22 — no check that the laser is off for the dark calibration
(remind the user).** At "Please turn off the laser" before the dark calibration the user
clicked OK with the laser still on: no warning came, and the picture flickered — the dark
calibration switches the camera's trigger off and runs at its maximum rate, while the Arduino
keeps flashing the laser, so some frames are lit and some not. The run was stopped by hand
(`SAvingFrames3_20261022_164607_cancelled`, 407 dark frames saved). A finished dark calibration
like that would subtract the laser light as "dark". The laser-off check exists only at the end
of the measurement (`_laser_off_check()`). Options: check the first dark frames against the
light seen in preview and stop with a warning; or the end check's logic reused at the start.
**Done 2026-10-26 — Done item 61** (the first option, plus a check of the finished dark mean).

---

---

#### F4 · HDF5 recording on its own thread *(was worklist task 14)* — **frames part ✅ 2026-10-21** (item 58)

*2026-10-21:* raw frames now have their own writer thread with a bounded queue (`core/frame_writer.py`, skip-and-count when full); the old `append_frame()` is gone. What is left: `recorder.append()` of the results rows on the GUI thread — harmless at the result rate.

`recorder.append()` and the gzip-compressing `recorder.append_frame()` run synchronously on
the GUI thread (`MainWindow._on_scos_result` / `_on_scos_frame`). Harmless at the result rate;
with Save Frames ticked, a slow disk becomes a GUI freeze. Move the writes behind a bounded
record queue so a slow disk becomes counted backpressure instead. **Done when:** Save Frames
does not stall the GUI, the queue is bounded, and the offline MATLAB tests still pass.

---

#### F5 · Reopenable, interactive session figure *(open question 7)*

Vika, 2026-10-07: `rBfi_fig.png` is fine as a first version. Later she wants a figure she
can reopen and zoom/pan **in Python** (not MATLAB). Either save an interactive HTML file
next to the PNG, or add a button that rebuilds the plot from the results file
(`timeVec`, `rBFi`). `--mock-h5` reads a results file but replays it in real time through the
live plot. It does not open a finished session in one step, so this is new work.
*Since U5 (2026-10-10):* `core/results_figure.build_results_figure()` already draws the
session figure from an open results file, so a "reopen" button or script only has to
open the file and show that figure in an interactive window (or save it as HTML).

---

#### F6 · Lost frames from `timeVec` gaps, for a camera without frame numbers *(2026-10-08)*

**Do only when needed:** when a camera's session logs "Camera reports no frame numbers" in
`app.log`. Until then this is not worth the code. The lab may use other cameras later, and
which ones is not known (user, 2026-10-08).

**The gap it fills.** Frames lost *before* reaching the app (all of Pylon's buffers full)
are counted only from `BlockID` gaps (`core/frame_clock.py`; D5, Done item 34). Pylon's
own `GetNumberOfSkippedImages()` does not count them under `OneByOne`. A camera without
block IDs therefore reports `frames_lost_camera = 0` whatever happened; only grabs that
arrive *broken* are still counted (`count_failed_grab()`). The queue counter "Dropped: N"
(`frames_dropped_queue`) does not depend on block IDs and is unaffected.

**Proposed shape: after the session, not live.** In `_finish_session()`, if the camera has
no block IDs **and** `time_source == "camera"`, count the gaps in `timeVec`. Compare each
gap with the recording's own **median** frame interval, not the FPS typed in the GUI, and
count `round(gap / median) − 1` lost frames for any gap above ~1.5 × median. Log the count
and write it to the results `metadata` as a *separate* field (e.g.
`frames_lost_from_timevec`). Do not add it to `frames_lost_camera`: the two are measured
differently.

**Why not live, and why not now** (discussed 2026-10-08):
- Lost frames do not reduce accuracy: each κ² comes from one frame, and every frame keeps
  its true capture time in `timeVec`. A loss is a missing point, so this is a setup health
  indicator, not part of the measurement.
- `timeVec` is already saved, so the check can always be done afterwards by hand. That is
  how the first rig session was checked: 1300 frames, all 50 ms apart.
- It needs the camera clock. A camera without block IDs may well lack timestamps too, and
  PC time jitters by milliseconds, so the check may not be possible exactly where it is needed.
- False alarms: in free run the camera may run slower than the FPS requested, and with a
  hardware trigger the Arduino sets the pace, so a pause in trigger pulses would look
  like lost frames. Using the median interval, after the run, avoids most of this. A
  live check would not.
- The app drives cameras only through pylon. Basler documents block IDs for both its
  USB (from 0) and GigE (from 1) cameras, so a camera without them is unlikely here.
- If one does turn up, the operator is already told ("no frame numbers" warning, made
  reliable by Done item 41), so the zero count is never silently trusted.

**Done when:** a session from a camera without block IDs records `frames_lost_from_timevec`.
A synthetic `timeVec` with known gaps gives the right count, and so does one with a
slightly slower than requested frame rate (no false losses). Cameras *with* block IDs are
unchanged.

---

## Execution order summary

```
▶ fixes from the first rig session (above): U1 → U2 → U3 → U4 → U5 → U8 → U9 → U6 → U10 → D8 → U11   (all ✅; U6 and U11 checked on the rig 2026-10-19)
▶ rig-session prep (above): all ✅ or closed 2026-10-20, except 3d (long run — before the first long recording) and 5b (not sent)
  then, after a successful session:
~~A1~~ → ~~A2~~ → ~~A3~~ → ~~A4~~ (float32 kept, 2026-10-20) → **A5 (corrected κ² 8 % off Vika's — before E5)**
  → ~~E1~~ → ~~E2~~ → ~~E3~~ → ~~E4~~ → E5 (tag v0 — after Vika's check of the math on saved frames)
  → B1 (overload dialog) → ~~B2~~ (with F1) → ~~B3~~ → B4 (camera reconnect)
  → C1 (scos_math) → C2 (frame_source ABC) → C3 (camera_source)
  → D1/D2/D3/D4/D7/D9/~~D11~~/D12 (any order; D9 low priority, test-only — decided 2026-10-20); D5 at the rig; ~~D6~~, ~~D8~~
  → ~~F1~~ (raw frames, 2026-10-21) → F2 (long sessions) → F3 (laser control) → F4 (results rows only) → F5 (reopenable figure)
  F6 (lost frames from timeVec gaps): only if a camera logs "no frame numbers"
```

---

## ✅ Done

| # | Task | Notes |
|---|------|-------|
| 1 | Auto-save to HDF5 wired into GUI | Folder picker on Start SCOS, status bar shows path |
| 2 | `HDF5Recorder` class (`core/recorder.py`) | Buffered writes, flushes every 300 points; `save_calibration()` and `append_frame()` also implemented |
| 3 | `closeEvent` flush | `recorder.close()` called before window exit |
| 4 | Metadata saved with session | Two groups, by instruction (2026-09-23). `Params` holds the supervisor's ten fields (`frameRate`, `exposureTime`, `gain`, `windowSize`, `ROI`, `bitDepth`, the three normalization fields, `gitCommit`); `metadata` holds engineering provenance only (`camera_sn`, `camera_model`, `gain_du_per_e`, `gain_source`). **`satCapacity` is excluded from both** — see the Critical Gotchas in CLAUDE.md. Built in `MainWindow._start_recorder()`, written by `core/recorder.py`. |
| 5 | rBFI normalization — "seconds" mode | `MEASURING_INIT` collects `norm_seconds` of BFI, computes mean, divides all subsequent values. **"Pulsation lower level" mode logs a warning and falls back to mean — see E1 below.** |
| 6 | Full state machine | `State` enum (IDLE → PREVIEW → DARK_CAL → BRIGHT_CAL → MEASURING_INIT → MEASURING → FINISHED/ERROR) wired in `gui/main_window.py` via `_set_state()`. Colored status indicator in GUI. |
| 7 | `core/pipeline.py` | `RealtimePipeline`: drop-oldest 20-frame `_input_q` + `ThreadPoolExecutor` (configurable workers) for parallel processing, with an `_inflight_sem` semaphore capping in-flight work so a slow patch can't grow memory without bound. Results emitted in submission order. `dropped_count` now shown in the GUI Info panel and logged on change. Intake moved off the GUI thread in item 20, which closed the last open half of A3. |
| 8 | `core/session.py` | `State` enum, `SessionConfig` dataclass, `DarkCalCollector` (Welford online stats), `BrightCalCollector` |
| 9 | Phase 1 — mock cameras | `mock_camera.py` (TIFF stack), `folder_camera.py` (real lab folder, auto-loads calibration), `h5_replay.py` (replays saved HDF5). CLI flags: `--mock-tiff`, `--mock-folder`, `--mock-h5` |
| 10 | `tools/synth_tiff.py` | Generates synthetic Rayleigh-distributed TIFF stacks for offline development |
| 11 | `logging` module | All debug/info output goes to `logging`; session log written to `app.log` |
| 12 | Math bugs fixed | `bright_var` (spVar) term added; unbiased variance estimator; `dark_var` spatially smoothed; `sat_capacity` corrected to 11117 e⁻ for a2A1920-160umPRO. **That last one was superseded on 2026-09-22:** G[DU/e] now always comes from `CamerasMeasuredGain.csv`, so no saturation capacity enters a measurement at all (`test_mode_sat_capacity` is synthetic-source only). |
| 13 | Phase 2 — real camera partial validation | App ran on real system at 40 Hz; processing time ~13 ms per frame (~12 ms headroom). Camera + laser streaming confirmed working. |
| 14 | Processing workers GUI control | `spn_workers` spinbox in SCOS group (range 1–8, default 3). Pipeline recreated on Start SCOS with the selected count. Tooltip shows machine core count. |
| 15 | `shrink_mask_for_window` — ROI edge fix | `processor.shrink_mask_for_window(mask, window)` erodes the ROI by `window//2+1` px. Applied at MEASURING_INIT start; shrunk mask used for κ² only, full mask kept for display. Erosion size logged. 4 new tests. |
| 16 | `GrabStrategy_OneByOne` + skipped-frame warning | `camera.py` now uses `OneByOne` + `MaxNumBuffer=20`. After each `RetrieveResult`, `GetNumberOfSkippedImages()` is checked and a `warning` signal emitted if > 0. Both test mocks updated. **Superseded by item 34 (2026-10-07):** under `OneByOne` that count never includes frames lost to full buffers, so the warning could not fire; lost frames are now counted from `BlockID` gaps. |
| 17 | Stop silently dropping data | `core/pipeline.py`'s emitter no longer swallows a worker exception with a bare `continue` — it's now logged (with traceback) and counted via `error_count`. `core/recorder.py.append()` no longer drops a row when κ² ≤ 0 — it keeps the row and stores `bfi=NaN` (via `n_invalid`) instead, so `time` stays evenly spaced for downstream FFT-based analysis. |
| 18 | Fix the ROI data race | `processor.py`: `set_roi()` and `process()` now publish/read one immutable `_RoiCrop` bundle via a single attribute assignment/read (atomic under the GIL) instead of 5 separate fields, so a worker thread in `process()` can no longer observe a torn mix of old/new ROI state. `gui/image_widget.py`: new `set_roi_locked()` disables the draggable circle (via `setEnabled`, which also disables its resize handles) and the Auto/Draw/Clear ROI buttons; wired into `gui/main_window.py`'s `_set_state()` so the ROI is locked for the duration of `MEASURING_INIT`/`MEASURING`. Verified the race empirically: the new concurrency test fails with ~28% of calls raising shape-mismatch errors against the old code, 0 against the fixed code. |
| 19 | Real sustained-overload test | `tests/test_pipeline.py` gains `TestSustainedOverload`, closing merged-worklist task 4 (the old `test_inflight_capped_under_sustained_overload` only covered item count, not memory). Memory is measured exactly, via a `weakref` to every submitted frame — the alive count *is* the number of frames the pipeline still holds — rather than by noisy RSS sampling; frames are the lab's real 700×700 uint16 size. Flooding a stalled 2-worker pipeline with 60 frames: **capped (shipped)** retains 25 frames / 24.5 MB with 35 drops counted; **uncapped (pre-fix)** retains all 60 / 58.8 MB with **0** drops counted — Review B's silent leak, reproduced. The uncapped case is kept as a permanent negative control so the cap can't be removed, nor the bound widened, without a red test. Verified it fails on broken code: neutering the semaphore made it fail with "retained 60 frames (58.8 MB)… expected <= 32". Tests only — no production code touched. Suite 194/194. |
| 20 | Frame intake off the GUI thread + capture-time timestamps | Closes merged-worklist task 5. `frame_ready` now carries `(frame, t_capture)` — `t_capture` is `time.monotonic()` taken at the grab, in all three real emitters (`camera.py`, `mock_camera.py`, `folder_camera.py`; `h5_replay._NullCamera` only stubs the signal and never emits). `RealtimePipeline.on_frame()` is direct-connected to that signal so intake executes on the **camera** thread: a full queue now blocks the grab loop (real backpressure, absorbed by Pylon's `MaxNumBuffer=20`) instead of freezing the GUI or piling frames into Qt's unbounded event queue. The wait is capped (`put_timeout_s`, 1.5 s) and a frame lost to that cap is counted — an unbounded wait would hang `CameraThread.stop()`, which calls `wait()` with no timeout, and with it the GUI. `overload_detected(depth)` fires once per episode at 80 % fill (re-arms below 50 %). `timeVec` is built from capture time relative to the first captured frame; `t0_wall` is latched alongside it for task 9's absolute `startTime`. Camera warnings (now the *expected* overload symptom) were demoted from a modal dialog to status bar + `app.log` during a session. **Verified end-to-end:** with the GUI handler stalled at 150 ms/frame against a 20 Hz source, `timeVec` keeps the camera's ~59 ms cadence; the pre-change design produced 151 ms gaps — a 20 Hz recording described as 6.6 Hz, which is a wrong heart rate out of the FFT. Suite 209/209; offline MATLAB dark/bright 4/4 at <2 %. |
| 21 | One session folder + real FINISHED transition | Closes merged-worklist task 8 (`gui/main_window.py` only). `_cal_output_folder` split into `_output_root` (parent, asked once per window) and `_session_folder` (a fresh `scos_<timestamp>/` per run) — dark cal, bright cal and results now land together, so two runs can't interleave files. The second `QFileDialog` inside `_start_recorder`, which used to pop up *mid-measurement*, is gone. Stop SCOS and the duration auto-stop both now go FINISHED → `_finish_session()` → PREVIEW; cancelling an unfinished calibration still goes straight to PREVIEW, since there are no results to finalize. `_finish_session()` deliberately runs while the recorder is still open — that is where E1/E2's close-time `rBFi` write, E3's laser popup and E4's figure save attach. New `tests/test_session_lifecycle.py` (7 tests); the two FINISHED tests were confirmed to fail against the old path. Suite 216/216. |
| 22 | Abort a run whose corrected κ² is never positive | Found in a mock-folder rehearsal on 2026-09-26: all 704 results came back at κ² ≈ −0.0029, so `bfi_raw = 1/κ²` was never computed, `_bfi_norm_buffer` stayed empty, and the session never advanced out of `MEASURING_INIT`. The app showed an empty plot and a frozen "Normalizing — 22.3 / 5 s" for 22 s and said nothing; stopping it produced a schema-valid `rBfi_results.h5` with an all-NaN `bfi` and no `rBFi`, the only trace being one WARNING in `app.log`. **That run's cause was specific to playback** (`--mock-folder` cannot switch the laser off, so the "dark" frames were the laser-on recording — `spIm` came out as symmetric noise around zero, proving both collectors drew from the same stream; see D6, the mode is not broken, Start SCOS just discards a good calibration). **The silence was not:** room light, a laser still settling, or OK clicked a beat early give the same picture on real hardware. New `MainWindow._abort_on_invalid_k2()` stops the run and shows an error naming the likely cause and the folder the raw data went to. It fires only once all three hold: no usable frame so far, `t` past `norm_seconds + 2 s`, and at least 10 results in — so a stalled pipeline or one stray late sample cannot abort a good run. The stop goes through `btn_start_scos.setChecked(False)`, the same funnel as the duration auto-stop, so an aborted session is finalized exactly like a manual one. New `tests/test_invalid_k2_guard.py` (8 tests), 4 of which were confirmed to fail with the guard removed. Suite 245/245. |
| 23 | Short-vs-long rBFi normalization (E1) | Closes worklist task 10. The reference (`SCOSvsTime_WithNoiseSubtraction_Ver2.m:498-514`) divides BFi by the **mean** of the baseline window when `timeVec(end) > 120`, and by its **5th percentile** otherwise; Python always used the mean, so every recording under two minutes — which is what a test run is — disagreed with MATLAB by the ratio of those two statistics, not by a rounding error. Three choices the reference leaves open were settled 2026-09-28: total duration decides (baseline window included, not subtracted); the window is the first `norm_seconds` from the GUI spinbox (MATLAB hardcodes 10 s, `session_tab` says the operator sets it); and picking "Pulsation lower level" forces the percentile at any length. **MATLAB's `prctile` is not numpy's default** — it interpolates between sorted values placed at (i−0.5)/n, which is numpy's `method="hazen"`; on [1,2,3,4] the 5th percentile is 1.0 by MATLAB and 1.15 by numpy's default, and that number divides every point in the file. The rules are pure functions in `core/session.py` (`choose_norm_method`, `normalization_constant`), so no math moved into the GUI. Because the curve is drawn live, the constant computed when the window closes is provisional and `MainWindow._finalize_normalization()` re-picks it at FINISHED, rescaling the plot by `provisional / final` instead of redrawing. `gui/plot_widget.py` now stores seconds and chooses its axis unit at render time, switching to minutes the moment a recording passes 120 s — it previously always divided by 60 and always said "min". Consequence worth knowing: `get_data()`, and therefore `scosTime` in the manual .mat/.npz export, is now in **seconds**, which is what MATLAB's `timeVec` holds. Two adjacent bugs fell out and are fixed here: the Save button exported `1/rBFi` under `scosData`, a key documented as κ², so it was off by the normalization constant; and the plot could end a session with up to a second of points still buffered, which matters because task 12 saves that figure. New `tests/test_normalization.py` (35 tests); six mutations checked — numpy's percentile instead of MATLAB's, `>=` instead of `>` at the threshold, the pulsation override removed, the finalizer not called at close, `scosData` back to `1/rBFi`, and the final render removed — each caught. Suite 280/280 fast, 4/4 slow. |
| 24 | A mock-folder rehearsal can reach MEASURING (D6) | Closes D6. Playback has no laser to switch off, so Start SCOS's dark calibration collected 60 frames of the laser-on recording and overwrote the calibration the auto-load had already put in the processor; corrected κ² was then negative in every frame and the run aborted. Two halves: `FolderMockCamera.set_playback_source("dark")` plays the `_dark` folder while the app is in `DARK_CAL` — every prompt, collector, dialog and file write still runs, they just get frames that really are dark; and `MainWindow._to_du()` converts a frame into the units `process()` computes in before any calibration array is built from it, because the Pylon-Viewer TIFFs store 10-bit data left-justified in uint16 and `dark_mean` was coming out 64× too large (that is where the recorded mean ROI intensity of −7872 DU came from). The intensity labels get the same treatment — they say "DU" and were showing raw counts. **Not a limit of the mode:** measured 2026-09-27, the auto-loaded arrays alone give κ²_corr ≈ 0.0104 against MATLAB's 0.0105. New `tests/test_playback_calibration.py` (12 tests); five mutations checked — never switches to dark, never switches back, collectors fed raw frames again, run loop ignores the source, a missing `_dark` folder accepted silently — each caught. Suite 292/292 fast, 4/4 slow. |
| 25 | Discard frames captured before the lighting changed | The calibration collectors run on the GUI thread behind a queued connection, so when the camera outruns that handler a backlog builds in Qt's event queue — and every frame in it was captured *before* the operator clicked OK on the laser prompt. Measured on the lab recording at 40 Hz with the backlog left in place: 11 of 60 collected "dark" frames were laser-on and **60 of 60 "bright" frames were dark**; `dark_var` came out at 72 instead of 5.6 and corrected κ² was negative in 353 of 355 points. All three emitters now count what they hand to Qt (`camera.frames_emitted`), `MainWindow` counts what it receives, and `_flush_stale_frames()` drops the difference after each prompt. The same mechanism exists on the rig, so this applies to every camera — but the 60–130 figure was measured headless, with the prompt returning instantly; a real modal dialog runs a nested event loop that keeps draining the queue, and the lab camera is 700×700 at 20 Hz against 2.4 Mpx at 40 Hz here. Treat it as cheap insurance rather than a measured rig problem; what is measured on both is that the backlog builds during the collection itself. **A whole session now completes:** headless run on the lab recording gives `dark_mean` = 99.307 DU and `dark_var` = 5.555, equal to streaming the dark folder offline, with κ²_corr positive in 315 of 315 points and `rBFi` written. New `tests/test_frame_flush.py` (7 tests); four mutations checked — skip removed, no flush before dark cal, none before bright cal, margin zeroed — each caught. Suite 299/299 fast, 4/4 slow. |
| 26 | `Mask.mat` ROI centre was read as [y x] | Found by checking the output of the first complete mock-folder rehearsal: `Params.ROI` did not describe the mask in the same file. `channels.Centers` is **[x y]**, as MATLAB's `imfindcircles` returns it, and the code read it the other way round — putting the ROI circle at (684, 1215) on a 1216-row frame, centred on the bottom edge. The mask that circle generates then replaces `totMask` through the `roi_changed` signal, so every κ² in a replayed session was computed over roughly the wrong half of the sensor. Pixel agreement with `totMask`: **49.4 % swapped, 99.3 % correct**. Real-camera sessions are unaffected — there is no `Mask.mat` and the operator sets the ROI in the GUI. The bare `except: pass` around this block, which would have hidden any failure in it, now logs. One test, and the swap put back fails it. Suite 300/300 fast, 4/4 slow. |
| 27 | Save the session figure (E4) | Closes E4 / worklist task 12. At FINISHED, `MainWindow._save_plot_figure()` writes the curve to `rBfi_fig.png` in the session folder, beside `rBfi_results.h5`. `session_tab` names the file `rBfi_fig.fig` — MATLAB's own figure format, which Python cannot write; PNG is the equivalent now that the tool is Python, and nothing is lost, because `timeVec` and `rBFi` sit in the results file in the same folder, so a real `.fig` can still be rebuilt in MATLAB from the same session. Confirmation is question 7 for the supervisor and the extension is one constant. Two things matter beyond "a file appears": it runs **after** `_finalize_normalization()`, so the figure shows the curve against the constant that was actually saved rather than the provisional one it was drawn with (that ordering is asserted, not assumed); and every failure is logged and swallowed — a missing PNG never justifies interfering with the close of a session whose HDF5 is already on disk. The export lives in `PlotWidget.save_png()`, rendered from the plot item so the Reset button stays out of the picture, at a fixed 1600 px so the file looks the same whether the window was maximised or tucked into a corner, and preceded by `render_now()` because the curve redraws on a 1 s timer and up to a second of points can be pending when a session ends. An empty plot writes nothing: a blank figure looks like a failed measurement. New `tests/test_figure_export.py` (9 tests, reading the PNG's IHDR chunk directly rather than pulling in an image library); five mutations checked — figure never saved at close, export failure no longer swallowed, empty plot writes a blank file, width taken from the window, buffered points not drawn first — each caught. Suite 319/319 fast, 4/4 slow. |
| 28 | Confirm the laser is off before a session is saved (E3) | Closes E3 / worklist task 11 (commit `cd4dbf3`). At the end of a measurement the operator is prompted — *"Measurement has ended. Please turn off the laser."* — and the app then checks that it actually went off: one fresh frame is captured and its mean ROI intensity must have fallen by ≥ 90 % against what the measurement was seeing. A session ruined by a laser left running is caught at the rig rather than during analysis weeks later. `_laser_off_check()` runs from the Stop branch of `_toggle_scos`, which both Stop SCOS and the duration auto-stop funnel through; it sits **before** `_set_state(FINISHED)`, so results from frames still in flight — all captured before the prompt — are not discarded, and before `_finish_session()` writes `rBFi`. **Both sides of the comparison are dark-subtracted**, mirroring `process()` exactly, and that is not a preference: the camera's black level (100 DU on this rig) does not go away when the laser does, so a comparison of raw DU could never fall by 90 % however completely the laser was switched off — the check would have warned on every run ever made. **The reference is the trailing mean over the last 5 s of results, not the final value.** `todo.md` E3 states that the last `mean_i` is "already available"; it is not — it was only ever a parameter of `_on_scos_result` and was never stored, hence `_intensity_history`, trimmed to the window and reset per run in `_finish_bright_cal` so a second Start SCOS cannot compare against the previous run's intensity. One frame's ROI mean is noisy, and a momentary shadow on the last frame would drag the reference low enough to let a laser that is still on pass. **Answering No to "Continue anyway?" re-runs the check and never discards the session** — the frames are already recorded, and Escape returns No too, which under this reading merely re-prompts where under a discard reading it would destroy a session. ROI-vs-whole-frame and trailing-mean-vs-last-value are questions 8 and 9 for the supervisor; both are answered here by a default and logged as such in `docs/open_questions.md`, each one constant away from changing (`_LASER_OFF_DROP_FRACTION`, `_LASER_OFF_REF_SECONDS`). Two things that would otherwise have looked like bugs at the rig: playback has no laser, so the check plays the `_dark` folder while it samples and restores `"main"` in the `finally` the way `_finish_dark_cal` does — without the restore, Stop SCOS leaves a mock-folder session parked on the dark folder and the next run's preview is black until Start SCOS is pressed again; and the whole step is wrapped in `try/except`, because a dialog or event-loop failure in the stop path would otherwise skip `_finish_session()` and cost the session its `rBFi`. For the same reason the outcome is **returned** and appended to the session-finished message rather than posted to the status bar, which `_stop_recorder()` and the closing message both overwrite — a failed check that reached only `app.log` is one the operator never saw. The dark-subtraction and ROI-mask shape guards `return None` into the "check skipped" path instead of falling through, since falling through would silently reintroduce the exact error the check exists to catch. New `tests/test_laser_off_check.py` (10 tests): the pass, the failure and its warning text, No-means-check-again, an empty reference, a camera that never delivers a frame, the playback restore, the ROI actually handed to the check by `_toggle_scos`, and a check that raises mid-stop still saving the session. Seven mutations checked 2026-09-28, each caught: dark subtraction removed so raw DU is compared (2 tests fail — a laser genuinely off no longer passes, and the warning quotes the wrong numbers); reference taken as the last value instead of the trailing mean; playback never restored to `"main"`; the `try/except` around the check removed, so a raising check costs the session its `rBFi`; No on "Continue anyway?" returning instead of re-checking; the per-run reset of `_intensity_history` removed, so run two compares against run one; and `laser_check_mask` captured after `_scos_mask` is cleared, which hands the check `None` and silently skips it. Suite 310/310 fast, 4/4 slow at the time of the commit. **Not yet exercised on real hardware:** the rig session of 2026-09-28 was stopped during `DARK_CAL` and never reached `MEASURING`, so the prompt has so far only ever been seen by the tests. |
| 29 | Recording name, and the opening dialogs in the protocol's order | Spotted by the user against `docs/SCOS_protocol.md`, and never in this backlog. The protocol reads G[DU/e] from the table, then *"Ask for recording name and location. Create appropriate folder"* (line 13), and only then the *"Please turn off the Laser"* pop-up (line 17); `docs/session_tab` says the same in other words — saving is arranged "at the very beginning of the session". The app asked for a location **after** the laser prompt and never asked for a name at all: the folder was always `scos_<timestamp>`, and no `QLineEdit` existed anywhere in `gui/`. Both are fixed. A **Recording name** field now sits at the top of the SCOS box and prefixes the session folder, `subject03_rest_20260928_164512`; the timestamp is never dropped, so repeating a name is harmless, and an empty field keeps the old `scos_<timestamp>` form. What is typed is repaired rather than rejected — spaces, slashes, colons and the rest of Windows' forbidden set become underscores, trailing dots and spaces go (they produce folders Windows cannot delete), and the prefix is capped at 64 characters — because an operator halfway through setting up a subject should not be stopped by a colon. The field locks with the other parameters at Start SCOS, since the folder is named there and a later edit would change nothing. **The order matters in the room, not only on paper:** everything done at the keyboard now happens before the lights go out, instead of leaving the operator standing in the dark hunting for a folder — and a Cancel there used to abort a run whose laser was already off. Because the folder is created before the prompt, cancelling it removes the folder again, via `rmdir`, which refuses to touch one that is not empty. The name is deliberately **not** persisted between launches (cf. B3): exposure carries over sensibly, a subject's name does not. New `tests/test_recording_name.py` (18 tests, including the dialog order asserted by recording which one opens first); six mutations checked — name ignored, timestamp dropped, name not sanitised, length cap removed, cancelled folder not cleaned up, field not locked — each caught. Suite 337/337 fast, 4/4 slow. |
| 30 | Remember the operator's settings between launches (B3) | Closes B3. Found 2026-09-26: `Dark Frames` set to 60, relaunch, the run used 600 — `_load_config()` read a file nothing ever wrote. `MainWindow._save_config()` now runs from `closeEvent()`, **after** `recorder.close()` and inside `try/except`, so a settings write can never cost a session its data. **Two files:** `scos_config.json` (tracked) holds the defaults and is never written; `scos_config.local.json` (gitignored) holds what the operator last used and overrides the defaults key by key — writing the tracked file would leave the rig's working tree permanently dirty and every `git pull` there a possible conflict. Delete the local file to reset. Written as JSON with `indent=4` and LF to a `.tmp` file and swapped in with `os.replace` (atomic), so a crash mid-write cannot break the next launch; unknown keys are kept; an unreadable file is logged and treated as empty. **Only a real camera's settings are saved:** in playback `_sync_params_from_camera()` reads the recording's exposure/gain/fps into the GUI, and saving those would start the next rig session with a recording's settings. The gate is positive — `CameraThread.persists_settings = True`, nothing else has it — with `--mock-folder`, `--mock-tiff` and HDF5 replay also excluded explicitly as a second layer. Checked first that restored values reach the camera: `_toggle_video` pushes them (ms → µs) before `start_capture()` and only then reads back. **The results folder** is saved as `output_root` but becomes only the folder dialog's *starting directory* (`_last_output_root`); `_output_root` stays `None` so the dialog still opens — a session must never land silently in the previous subject's folder. The recording name is not saved (cf. item 29). `tests/conftest.py` gains an autouse `isolated_config` fixture: every test gets a private copy of the defaults via `SCOS_CONFIG_DIR`, so the suite never writes the real files (verified: `scos_config.json` checksum unchanged and no local file in the repo after a full run). New `tests/test_settings_persistence.py` (18 tests); nine mutations checked — gate removed, playback layer removed, `os.replace` skipped, saved folder loaded into `_output_root`, dialog not given the folder, save not called on close, local override not applied, CRLF output, `n_dark_frames` dropped — each caught. Suite 359/359. |
| 31 | Disable "Save Frames" until raw-frame saving is ready (rig prep 1c) | Commit `10c74ce`, 2026-10-04. Replaced B2-lite (a free-space check before recording) after the user asked why a disk check was needed at all when raw frames are not saved: without them a session writes very little — `rBfi_results.h5` holds five numbers per frame (~10 MB for 3 h at 20 Hz), `Calibration.h5` a few frame-sized arrays (~10–20 MB at 700 × 700), plus a small PNG — under 50 MB, so the check would almost never fire. The one way to fill a disk was the checkbox itself: it was already wired to `HDF5Recorder.append_frame()`, writing every frame (~1 MB, ~70 GB/hour at 20 Hz) synchronously on the GUI thread (F4) with no space check (B2). Raw frames are not wanted yet, so the box is greyed out with a tooltip saying why. It stays **visible** because `SCOS_protocol.md:9` lists it among the SCOS parameters. Re-enabling it is part of F1, with B2 and F4. New `tests/test_save_frames_disabled.py` (2 tests: disabled, unchecked and visible at start-up; neither unlocking the parameters nor passing through every `State` re-enables it); mutation checked — with `setEnabled(False)` removed both fail. Suite 357/357 fast. |
| 32 | Headless rehearsal harness `tools/rehearsal.py` (rig prep 3a) | Commits `7c4ad23` (harness) and `4f04963` (docs), 2026-10-05/06. Replaces the gitignored `scratch/e2e_rehearsal.py`, which printed values but asserted nothing, once hung on a real "Measurement Ended" dialog, and answered every `question()` with Ok — on "Laser May Still Be On" Ok means "check again", an endless loop. Drives the real `MainWindow` through a whole `--mock-folder` session (Start Video → auto-load → Start SCOS → dark → bright → normalization → measure → Stop) and checks ~30 things: protocol dialog order, dark cal from dark frames and bright cal from laser-on frames, laser-off check passed (return value captured — the status bar loses it, K1), exactly three session files, exactly the expected `rBfi_results.h5` entries and the ten `Params` with the recording's values and the right normalization method, equal series lengths, increasing `timeVec`, κ²_corr > 0, no NaN in rBFi, no `satCapacity` in either file, no uncaught exceptions. Every dialog is stubbed and answered **by title**; an unknown one is refused and reported, never left open; a faulthandler watchdog (`--timeout`) dumps all stacks and exits; config isolated to the committed defaults. Scenarios are hook sets in `SCENARIOS` (only `normal`; 3b–3d add theirs). Exit 0 = all passed. **Measured:** 60 frames — all pass, κ²_corr 0.0070–0.0076 (304/304 positive), ≈ 50 s; 600 frames — all pass, κ²_corr 0.0085 (305/305), 155 s, dark cal 69 s, bright 41 s, 1555 frames flushed at the bright prompt (→ D8). **Mutations checked:** `FolderMockCamera.set_playback_source` patched to a no-op (dark cal from laser-on frames) → 5 FAIL, exit 1; `--timeout 15` → stacks dumped, exit 1. No pytest for the harness itself — it needs the lab recording; fast suite 357 passed. |
| 33 | Slowdown / backpressure scenario (rig prep 3b) | `tools/rehearsal.py --scenario slowdown`, 2026-10-06. The same whole session as `normal`, with two differences. **Playback at 10 Hz:** this PC's pipeline manages only ≈ 21 frames/s of 2.4 Mpx, so at 40 Hz even `normal` is overloaded from ≈ 1.5 s into normalization and there is no clean baseline to slow down from. The rate is set on the camera, not in the FPS box, so `Params.frameRate` still matches the recording. **Inside MEASURING:** 3 s at full speed → 6 s with every `process()` 0.6 s slower (≈ 4 frames/s against 10) → one 8 s stall → 4 s slow again → Stop SCOS pressed while still overloaded. Adds 15 scenario checks, plus one shutdown check that every scenario now runs ("pipeline thread had stopped when the window closed"). **Measured** (60 calibration frames, ≈ 115 s a run, 4 runs; memory numbers from the last 3): no overload at full speed; `overload_detected` fired exactly once (queue 16/20, never back below 15); **0 dropped while merely slow**, because the blocking intake slowed capture instead; the stall dropped 4 frames, which were counted, shown on the "Dropped" label and logged; every `timeVec` value is exactly a capture stamp minus t0, although results reached the GUI up to 12.9 s after capture, and the 4 frames missing from `timeVec` are exactly the 4 counted drops. Memory is judged by phase medians, because single samples spike by up to ≈ 150 MB: at most +83 MB above full speed (bound 251 MB = 2 × 28 held frames), and +0 to +1 MB from a full queue to the end. **Two real failures, left failing for 4a:** K4, the overload warning is overwritten by "Frame #…" within 2.5 s; K5, the pipeline thread was still running 3.7 s after the window closed. **Mutations (each caught; rerun any with `tools/rehearsal_mutations.py <name>`):** overload latch removed → fired 44×; `timeVec` stamped on arrival at the GUI → 104 of 104 values are not capture stamps, and 132 frames missing against 4 counted; drops not counted → 4 missing against 0 counted, and nothing logged; in-flight cap removed → no overload warning, phase median +410 MB, +312 MB after the queue "filled". **Cannot show:** the real camera's buffering and how it stamps frames under overload (D5 note). Also corrected `simulation_checklist.md` B8 (expect FPS of roughly 15–25 and one overload message on this PC, not drops) and added K4/K5 to its known issues. Fast suite: 357 passed. |
| 34 | Real camera: capture time from the camera's clock, lost frames counted (D5 follow-up to 3b) | 2026-10-07, the user's decision — the two real-camera problems 3b exposed, fixed before the rig session instead of after. **Problem 1, timestamps:** `camera.py` stamped a frame with the PC clock when it was *retrieved* from Pylon's buffer, so frames that waited there during a stall got bunched stamps. **Problem 2, lost frames:** under `GrabStrategy_OneByOne`, `GetNumberOfSkippedImages()` does not count frames lost to full buffers (Basler's API reference), so they were counted nowhere. **Fix:** new `core/frame_clock.py` (`FrameClock`, pure Python), used by `CameraThread.run()` for every grab result. Capture time = the result's `TimeStamp` (camera ticks at the start of exposure) converted to seconds on the `time.monotonic()` scale. It is used only after it agrees with the PC clock within 1 % over 5 s, for the reported tick rate or one of the Basler rates (1 GHz / 125 MHz). The check compares the *least delayed* frames of the first and last second, so one late frame or Windows' coarse pre-3.13 `time.monotonic()` cannot reject a good camera. Otherwise the PC stamp stays and `app.log` says why, so a wrong tick rate can never stretch `timeVec`. Lost frames = jumps in `BlockID` (GigE 1…65535 wrap handled; a counter restart after a grab restart is not a loss) plus incomplete grabs, each counted once. A camera without frame numbers is logged once ("Camera reports no frame numbers"). Visible as a warning at most once a second ("Camera: N frame(s) lost — Pylon buffers full or transfer failed"), on the label as "Dropped: N + M lost at camera", and in the results file's `metadata`: `time_source`, `frames_lost_camera`, `frames_dropped_queue` (provenance, so not in `Params`). The old skipped-images warning, which could never fire under `OneByOne`, is gone. **Tests:** `tests/test_frame_clock.py` (25: IDs, wrap, restart, failed grabs, clock check, the backlog test — frames retrieved in a burst keep their 50 ms exposure spacing, wrong or unknown tick rate → PC, no timestamps → PC, clock backwards → PC, a 100 ms-late first frame and 30 ms jitter on a 15.6 ms clock still accepted); `tests/test_camera.py` +4, running the real `run()` loop on a fake Pylon camera; `tests/test_results_schema.py` checks the three metadata fields. **Real pypylon 26.02.1, via Pylon's camera emulator:** `GrabResult` has `TimeStamp` and `BlockID`, and `CameraThread.run()` grabbed 60 frames in 3 s at 20 Hz. The emulator has no timestamps or frame numbers, so it fell back to the PC clock and logged both lines. **Mutations, each caught:** grab loop back on PC stamps; wrap treated as a restart; failed grab counted twice; tick rate not checked; check back to first-vs-last frame; frame accounting not written. Fast suite 386 passed, slow MATLAB tests 4/4, `normal` rehearsal passes (file says `time_source = "pc"`, 0 lost). **Not testable without the camera** — verified at the rig by D5 (step 5a). |
| 35 | K5 — the pipeline no longer works through a backlog nobody will use when it is stopped | 2026-10-07, the user's decision to fix it now rather than after the session. Found by the 3b slowdown rehearsal: `RealtimePipeline.stop()` put its stop marker *behind* the queued frames, so the pipeline first processed up to 20 queued + 6 in-flight frames whose results nothing would receive. With processing slowed to ≈ 0.75 s a frame it outlived `MainWindow.closeEvent`'s 2 s wait by 3.7 s and died in Python's shutdown ("cannot schedule new futures after interpreter shutdown"). **Fix:** `stop(discard_queued=True)`. It empties the input queue (new `_DropOldestQueue.clear()`, which also wakes a camera thread blocked in `put()`), cancels frames handed to the worker pool but not started, and skips the one the dispatcher may be holding. Only frames already inside `process()` finish — at most one per worker. Discarded frames count neither as drops nor as errors (a cancelled future is not an error), and `app.log` says "Pipeline stopping — N frame(s) discarded unprocessed". Used in the two places where those results can never be used: `closeEvent`, and Start SCOS replacing the previous pipeline. Plain `stop()` keeps draining, as `test_clean_shutdown_no_lost_results` requires. Stop SCOS is unchanged: it only closes intake. **Measured:** slowdown rehearsal — shutdown check now passes (21 frames discarded at close), only K4 still fails. **Tests:** `tests/test_pipeline.py` +4: stop finishes after one processing round (< 0.6 s against ≈ 2.6 s to drain), no drops or errors added, default stop still delivers everything, `clear()` counts and wakes a blocked producer. Two mutations caught: discard flag ignored; cancelled futures counted as errors. Full suite 394 passed, including the 4 slow MATLAB tests (`core/pipeline.py` is on the G1 list); `normal` rehearsal passes. |
| 36 | Overload-recovery scenario (rig prep 3c) | `tools/rehearsal.py --scenario recovery`, 2026-10-07. 3b stopped while still overloaded, so it never showed the pipeline getting well again. Same session and 10 Hz playback as `slowdown`, inside MEASURING: 3 s full speed → **episode 1** (6 s slowed + the 8 s stall, kept because slowing alone drops nothing and "drops stop" would then check nothing) → **recover** (full speed: wait for the queue to drain to ≤ 10 of 20, then 4 s) → **episode 2** (slowed, no stall, until the second warning + 2 s) → **recover2** (full speed, 4 s) → Stop SCOS. Warnings are assigned to time windows, not phase names, because the signal reaches the GUI through the event queue. 15 scenario checks plus 3b's six `timeVec` and memory checks (refactored into shared methods, unchanged for `slowdown`). The K4 check is left to `slowdown`, so a clean `recovery` run exits 0. **Measured** (60 calibration frames, ≈ 95 s a run, 4 complete runs): one warning per episode (queue 16/20 both times), none at baseline or while recovered; queue below the 50 % re-arm mark 0.3–0.4 s after speed came back; the pipeline's flag reset; drop count frozen at the stall's 4–5 from the first recovery to Stop SCOS, including the whole of episode 2; recovered results reach the GUI ≤ 0.21–0.25 s after capture (0.08–0.23 s before the slowdown) at 9.3–10.0 results/s against 10 Hz (lag and rate from the 3 runs whose full output was kept). Memory after recovery is printed, not checked: back to within +8 to +42 MB of full speed, but a limit tight enough to mean anything failed on the ≈ 150 MB worker spikes (tried, and dropped). **Mutations:** `tools/rehearsal_mutations.py never-rearm` (flag never resets) → "overload flag reset" and "episode 2 fired again" fail (fired 0×); `latch --scenario recovery` → fired 21× / 17×; each mutation now carries its own default scenario. Not shown able to fail (no mutation): queue drained, drops stop, no warning while recovered, lag/rate restored — simple comparisons, and "drops stop" is guarded against passing on nothing by requiring the stall's drops first. **Not testable in a rehearsal — the 50 % mark itself:** while slowed, the blocking intake keeps the queue at 19–20 of 20, so a wrong low mark never shows; new `test_overload_warning_rearms_only_below_half_full` drives `_check_overload()` depth by depth, and fails with the low mark at 0.4, 0.6 or 0.8. **3b check fixed:** the K4 check read the status bar once, 3 s after the warning, but "Frame #…" is rewritten only when a display frame arrives (≤ every 2.5 s, during the stall ≈ every 1.5 s), so it raced — one `slowdown` run passed it. It now records every `messageChanged` and fails if the warning is replaced within 3 s ("replaced after 0.2 s by 'Frame #997 …'"). K4 itself is unchanged and still for 4a. One run timed out before the scenario began (folder calibration load > 300 s while a pytest run took 6 min for 2 tests, so the machine was busy at that moment); not reproduced. |
| 37 | rBFi on an early stop (rig prep 4c, open question 11) | Vika's answer of 2026-10-07: a run stopped before its baseline window closes is normalized "on whatever data exists". Until now such a run wrote raw `bfi` and **no `rBFi`**, and an empty figure, which is exactly what a short test run at the rig would produce. New `MainWindow._normalize_early_stop()`, called at the top of `_finalize_normalization()` when there is no constant yet but `_bfi_norm_buffer` holds BFi values. The whole run is the baseline: the constant comes from every buffered value; the method is picked once, on the final length, by the same `choose_norm_method()`. Because the spinbox stops at 60 s, an early stop is always under the 120 s rule and gets `percentile5`. Three details matter for accuracy and for what the operator sees. (1) **`normalizationWindowSec` is the span actually used**, `_last_result_t`, not the spinbox's `norm_seconds`. A new per-run field `_bfi_norm_window_s` carries it to `_write_rbfi()`; the normal window close sets it to `norm_seconds` as before, and it is reset with the other per-run state in `_finish_bright_cal()`. (2) MEASURING_INIT never plots, so the buffered points are drawn before `render_now()`; otherwise `rBfi_fig.png` would be blank. (3) The status label changes from the frozen "Normalizing — 3.0 / 5 s" countdown to "Normalized on 3.0 s (stopped early)". **Unchanged:** a run with no valid BFi at all still writes no `rBFi`, and closing the window or a crash still leaves raw BFi only (answer 5); `closeEvent` never reaches `_finish_session()`. `tests/test_normalization.py`: the old `test_an_unnormalized_session_is_left_alone`, which asserted the old rule, became class `TestEarlyStop` (6 tests: rBFi on disk, window = 3 s not 5, points plotted and label, the real Stop path through `_toggle_scos` with the laser-off check stubbed, no valid BFi → no rBFi, a completed window still writes 5 s). Six mutations run: early normalization removed, window from the spinbox, points not plotted, `_write_rbfi` passing `norm_seconds`, normal close not setting the window — each caught; "early stop always uses the mean" survives because `_finalize_normalization()` re-picks the method on the final length right after, so the output is identical (equivalent mutant). Suite 396/396 fast; `tools/rehearsal.py --cal-frames 60` (normal) all checks pass. **Not covered automatically:** the real modal "Measurement Ended" dialog's nested event loop during an early stop (the rehearsal always waits for normalization to finish) — checked by hand at 3f, `simulation_checklist.md` F5, updated with the new expected result. |
| 38 | Rehearsal output verification (rig prep 3e) | The 3e row asked for checks after every scenario that `rBFi` is present, all series are the same length, `Params` has its ten fields, the figure exists and `Calibration.h5` has both groups. **All of that already existed:** `verify_outputs()` in `tools/rehearsal.py` was written in 3a and runs at the end of every scenario. What was missing was proof that the numbers are *right*, not just present — which is what the measurement-accuracy rule cares about. Added to `verify_outputs()` (it now also gets the window and the run's start time): (1) `bfi` = 1/`k2_corr` where κ² > 0 and NaN elsewhere; (2) `rBFi` = `bfi` / `Params.normalizationConstant` to 1e-12 — Done item 23 found exactly this kind of wrong-divisor bug in the Save export; (3) **the constant recomputed independently from the file's own rows**: every valid row up to and including the first valid one at or past `norm_seconds` (the handler appends a point before testing whether the window closed), through `choose_norm_method(timeVec(end))` and `normalization_constant()` — this tests *which samples* went into the baseline; the percentile maths has its own unit tests; (4) `metadata` has all seven fields, `frames_dropped_queue` equals the GUI's counter, `gain_du_per_e` equals the processor's, `time_source` is what the camera reports ("pc" for playback); (5) `Params.ROI` equals the GUI's circle and `gitCommit` is a real hash; (6) `startTime` parses as MATLAB's `dd-Mmm-yyyy HH:MM:SS` and falls inside the run; (7) `Calibration.h5` holds `dark/{mean_dark,var_dark,mask}` and `bright/{spIm,spVar}`, each a finite frame-sized image, with `n_frames` equal to the spinbox. Three new mutations in `tools/rehearsal_mutations.py`, each run alone and each caught by the check it targets: `divisor` (rBFi written with a 0.1 % wrong divisor → "rBFi = bfi / …" 9.99e-04 off), `baseline` (the constant computed without the window's last sample → recomputed 99.7134 vs Params 99.7075), `meta-drops` under `slowdown` (dropped count written as 0 → "= 0 (GUI counter 5)"). Final runs, one at a time, `--cal-frames 60`: `normal` all pass; `recovery` all pass; `slowdown` fails only **K4**, as before (open, fixed in 4a). Fast suite 396/396. |
| 39 | K1–K4 — status messages stay on screen; leaving calibration early puts everything back (rig prep 4a) | 2026-10-07. Four bugs found while writing `simulation_checklist.md` (K1–K3) and by the 3b slowdown rehearsal (K4). **K1/K4, status-bar messages vanished:** `_on_display_frame` wrote "Frame #… shape=… min=… max=…" with `showMessage()`, up to 30 times a second in preview and every 2.5 s while measuring, so the session folder, the closing "Session finished → … \| laser-off note" (the only on-screen trace of a skipped laser-off check) and the overload warning were replaced almost at once. **Fix:** the readout moved to its own `QLabel` added with `addPermanentWidget()` (right end of the status bar; `showMessage()` never hides it), shortened to "Frame #N  min=…  max=…" because it takes width from the message area (shape is on the Size label). Messages now stay until the next event replaces them, so the overload warning got the time it fired, "(at HH:MM:SS)", to read as a past event after recovery. **K2/K3, early exits from calibration:** five exits — Cancel at the bright prompt, a dark- or bright-collector error, Stop SCOS in `DARK_CAL` or `BRIGHT_CAL` (Stop Video presses Stop SCOS) — each undid its own subset: the parameters stayed locked after a Cancel or an error, playback stayed on the dark folder after a stop in `DARK_CAL`, a dark-collector error also left the external trigger off, and the session folder stayed with a dark-only `Calibration.h5`. **Fix:** all five go through new `MainWindow._abandon_calibration()`: state to PREVIEW and collectors dropped **first**, then playback back to `"main"`, the trigger restored only while dark calibration still had it off (later exits: `_finish_dark_cal` already did, and setting it twice would make a real camera restart grabbing), button and parameters reset, and the folder handled by new `_discard_session_folder()` — **removed when empty, renamed `<name>_cancelled` when it already holds a partial calibration** (the user's choice, 2026-10-07: nothing measured is deleted, and it can't be mistaken for a finished session); the status bar names the renamed folder. The Cancel at the dark prompt uses the same helper, and so does closing the window during calibration (`closeEvent` never goes through Stop SCOS; it only needs the folder step). **Also fixed, found on the way:** the two error paths showed their `critical()` dialog *before* leaving `DARK_CAL`/`BRIGHT_CAL` with a full collector still set; a real modal dialog's nested event loop keeps delivering frames, `done` stays True, so each frame re-ran `_finish_*_cal` and stacked another dialog. Undo first, dialog second, now. **Tests:** `tests/test_status_messages.py` (5: a message survives preview frames, the readout still updates, the overload warning survives measurement frames and carries its time, the closing message from a real Stop SCOS survives frames) and `tests/test_calibration_exits.py` (9: each of the exits including Stop Video and closing the window, the trigger restored or not, folder removed or renamed with the file inside, and no second error dialog when frames arrive during the first) — the first 13 were run against the old code and all failed; the close-window test was added after. **Rehearsal:** new check in every scenario — the closing message is still on the status bar 3 s after Stop SCOS, over the frames displayed meanwhile (preview now plays 3 s before Stop Video); new mutation `tools/rehearsal_mutations.py frame-text` (frame text written to the status bar again) → exit 1, fails exactly the K1 check ("now 'Frame #695'") and the K4 check ("replaced after 0.0 s"). Final runs, one at a time, `--cal-frames 60`: `slowdown` **all pass** for the first time (99 s), `normal` all pass, `recovery` all pass. Fast suite 410/410; the 4 slow MATLAB tests were not rerun — they use only the collectors in `core/session.py`, not the `MainWindow` methods changed here. **Not covered automatically:** real modal dialogs and whether a long path is cut off by the readout field — `simulation_checklist.md` B11, F3, F4, F4b updated for 3f. |
| 40 | Bright calibration with the subject in place (2026-10-08) | User's instruction: the bright frames are taken **with the subject in the measurement area**. `SCOS_protocol.md` only ever said "Please turn on the Laser"; "remove the subject" had been added by the app. It is also how the MATLAB reference was made: `smoothingCoefficients.mat` comes from the recording's own frames (`tests/test_bright_cal_offline.py`, which matches MATLAB per frame within 2 % that way). The prompt in `MainWindow._start_bright_cal()` now reads "Please turn on the laser. Keep the subject in the measurement area. … The measurement starts automatically when this calibration ends." — title unchanged, since the rehearsal and the dialog-order test find dialogs by title. New `tests/test_bright_prompt.py` (4). Side effect: the no-pause problem found while writing 5a (the baseline starting while the subject is put back) is gone. **Also corrected a wrong claim** in CLAUDE.md, `simulation_checklist.md` item 3, todo row 2 and `tests/test_playback_calibration.py`: playback κ²_corr was said to be well below MATLAB ("0.0105") because the subject made `spVar` 2.3× too large. Measured 2026-10-08 on the hand-run session of 2026-10-07 (600 + 600 frames): κ²_corr 0.0083 vs `LocalStd7x7_corr.mat` mean 0.00838; its calibration on the first 10 frames gives −0.7 % vs MATLAB with `totMask`, the circle ROI or the shrunk ROI alike; `spVar` 0.513 vs `smoothingCoefficients.mat` 0.528 in `totMask`. Only the 60-frame shortfall is real (open question 17). |
| 41 | USB camera's frame 0 no longer read as "no frame numbers" (2026-10-08) | First run on the rig (a2A1920-160umBAS, SN 40075248, **USB**) logged "Camera reports no frame numbers (BlockID 0)" 38 ms after capture started — on the first frame. Basler's pylon C API reference (`PylonGrabResult_t::BlockID`): USB cameras number frames from **0**, GigE from 1, and 0 *can* also mean "not supported". `core/frame_clock.py` treated every 0 as unsupported. Now a 0 on the first frame after (re)start is frame number 0; only a **second 0 in a row** means the camera has none (warned once, failed grabs then counted directly; kept across restarts); a lone 0 mid-stream (USB counter restart) is skipped silently. Effect on the 2026-10-08 session: only the misleading warning — counting worked from frame 2 on, and its timeVec had no gaps. The TIFF names starting at `frame001` come from MATLAB's `WriteTiffSeq.m` loop index, not from the camera. Tests: `TestUsbNumbersFromZero` in `tests/test_frame_clock.py` (7; 4 fail on the old code). Still to confirm on the camera: grab a few frames and print `BlockID` (expected 0, 1, 2, …). |
| 42 | Laser-safety warning at startup (U1, 2026-10-09) | With the real camera, `main.py` now shows a "Laser safety" warning **before anything else opens** (camera, main window): the subject wears laser-safety goggles for the whole measurement; the probe comes off only after checking the laser is off — its red indicator light not lit; and only by pulling the rubber strap backwards (the user's wording after the first rig session). **I confirm** continues; **Exit**, Escape, Enter (Exit is the default button, so confirming takes a deliberate click — user's decision) or the title-bar X closes the app before a window or the camera exists, so no settings are saved. Both answers go into `app.log` ("Laser-safety warning confirmed by the operator" / "… not confirmed — the app closes"), which is copied into the session folder. **Real camera only** (user's decision 2026-10-09): `--mock-folder`, `--mock-tiff` and `--mock-h5` have no laser and open without it. Code: `gui/safety_dialog.py` (`LaserSafetyDialog`, `confirm_laser_safety()`), `main.py` real-camera branch. A QDialog of its own, 960 px wide with wide margins and large text (user's request: a QMessageBox caps its width at about 800 px on the rig PC's 1280 × 752 screen and looked crowded). `tests/conftest.py` now also blocks `exec()` on any QDialog and on QMessageBox (a hand-built window was not covered). Tests: `tests/test_laser_safety.py` (11 — wording, size, each answer including Escape and Enter, and `main.py`'s wiring read from its source, because importing `main.py` would overwrite `app.log`); checked by mutation: always-confirm → 4 fail; Exit accepting → 1; I confirm as the default button → 1; the call skipped in `main.py` → 1. Tried by the user on the rig PC (with the earlier message-box version): the warning opens in front; Exit closes with nothing saved, I confirm opens the main window. |
| 43 | "Laser may still be on" made unmissable; probe-removal window after the save (U2, 2026-10-09) | **The failure window** (`MainWindow._laser_off_check()`, E3) was a plain `QMessageBox.question` with Yes/No. It is now `safety_dialog.LaserStillOnDialog`: a red window (dark-red background, bright-red frame), heading **"LASER MAY STILL BE ON"**, the measured and expected numbers, buttons **Check again** (default; Enter, Escape and the X mean the same) and **Continue anyway** (a deliberate click). Behaviour unchanged: Check again re-prompts and measures again, Continue anyway saves with "LASER-OFF CHECK FAILED" in the closing message. **The probe-removal window (the user's idea):** once the session is on disk — after `_finish_session()` and `_stop_recorder()`, so a recording never waits for a click — `MainWindow._show_probe_removal()` opens `safety_dialog.ProbeRemovalDialog` for how the check ended (`_laser_off_outcome`, reset at every stop): **passed** → green "Laser is off — you may remove the probe"; **could not run** → amber, check the red light yourself; **failed + Continue anyway** → red "do NOT remove the probe yet" (the user's decisions for the last two). Every one repeats U1's two rules — the laser's red indicator light not lit, the probe off only by pulling the rubber strap backwards — even after a passed check, because the camera going dark is not the rule, the light is. Shown in playback too (the laser-off check runs there against the dark folder), so the rehearsal and the simulation checklist cover it. A failure to show it is logged, never raised (the data is already saved). **Two more cases (the user's request, same day):** (1) a run that ends during the **bright calibration** — Stop SCOS (also via Stop Video), Cancel at the bright prompt (the laser may already be on), a bright-calibration error (after its error box), or closing the window (after the camera has stopped, so frames cannot finish the calibration while the window is open) — shows a red "Calibration stopped — the laser is probably still on" window (`PROBE_CANCELLED`); not during the dark calibration, when the laser is off. (2) **Closing the window mid-measurement** now runs the Stop SCOS path first, while the camera still runs: laser prompt and check, `rBFi` and the figure written, probe window. Before, `closeEvent` only closed the recorder — no laser prompt and no `rBFi`. Test fixtures that close a window parked in MEASURING set PREVIEW first, or teardown runs that whole path. All three windows share `_SafetyWindow` with U1's startup warning (960 px, wide margins; U1's look unchanged). `main_window.py` imports `safety_dialog` as a module so tests and `tools/rehearsal.py` can replace its windows; the rehearsal now stubs `QDialog.exec` by window title and expects "Laser Is Off — Remove the Probe" after "Measurement Ended". Tests: `test_laser_safety.py` +22 (wording, colours, buttons, every answer, all five windows fit the 1280 × 752 screen), `test_laser_off_check.py` reworked + 14 (outcome per path, window per outcome, the window comes after the save, no carry-over between runs, none after a cancelled calibration, a broken window does not crash Stop; the bright-calibration exits, closing mid-measurement and mid-calibration), `test_normalization.py` (early stop also shows it). Mutation-checked: outcome not set on pass → 1 fails; window shown before the save → 6 fail; each new case removed, or the closing window shown before the camera stops → 1 fails each. Fast suite 474/474; `rehearsal.py --cal-frames 60` all checks pass. |
| 44 | Main window fits the screen; parameter fields no longer overlap (U3 + U4, 2026-10-10) | **Cause — not what the U3 row guessed:** no label was pushing the window. The right-hand control panel, one field per row, needed **861 px** of height and the whole window 901; the rig PC's screen (1280 × 800 at 150 % scaling, less the taskbar) has **752**. Measured on the rig PC with the window shown: after label changes the un-maximized window stayed at that 931 px minimum (short texts; a window that was never shown does not update its sizes, so earlier hidden-window numbers did not count). `main.py` opens the window maximized, which squeezed every field below its minimum height, so they were clipped and overlapped — that is **U4**, the same problem. Un-maximized, Qt would not let the window be shorter than 931 px with its title bar, so the bottom ~180 px — the status bar with its warnings — were off the screen. (The exact "grows back after a few seconds" timing was not reproduced; every way of showing the old window ended either squeezed or 931 px tall.) **Fix, in `gui/main_window.py`:** (1) the fields sit **two to a row** (the user's choice of two layouts): Camera — Format | Exposure (ms), Gain (dB) | Frame Rate (Hz), Trigger Delay (µs) | External Trigger; Start Video without its own "Acquisition" box; SCOS — Recording name, Dark | Bright Frames, Window Size | Processing workers, Measuring duration (min) (was "(in minutes)"), Norm. type; Save Frames beside Save Data; the Info box in two columns. Panel 861 → **577 px**; in the maximized window it gets 690, so **~113 px spare**; panel ~450 px wide instead of ~275. Units in every label unchanged (ms vs µs matters). **Also fixed:** in the locked look during a measurement (`_LOCKED_GROUP_STYLE`) the box titles "Camera" and "SCOS" sat on the first row of fields — a styled QGroupBox loses its title room; `margin-top` and the title's position give it back (pre-existing, seen in a dark-style screenshot). Locking still shifts the panel slightly (≈ 30 px wider, the boxes ≈ 11 px shorter — the sheet also restyles the spin boxes), as before. (2) **Safety net:** the panel sits in a vertical-only scroll area (`_ControlsScrollArea`), so a different screen or scaling can never push the window off-screen again; on the rig screen no scrollbar appears. It asks for the panel's full width (no horizontal scrollbar, so a narrower area would clip) and an event filter passes a panel size change on at once. (3) `_fit_to_screen()`: "Restore Down" now gives the contents' preferred size capped to the screen (1069 × 704 on the rig PC); left to Qt it was ⅔ of the screen, 853 × 533 — too short, the panel scrolled. Tests: new `tests/test_window_fits_screen.py` (4 — window minimum fits 1280 × 752 with a session's longest label texts; the panel fits the maximized window without scrolling; the restored window fits with no scrollbar; the scroll area widens with the panel) — windows laid out but not drawn on screen (`WA_DontShowOnScreen`), as the suite runs in the pre-commit hook; the long-texts case also locks the boxes. Mutation-checked: no `_fit_to_screen()` → 1 fails; no width override → 1 fails. Sizes are the rig PC's (this PC), logical px at 150 %. Fast suite 478/478; `rehearsal.py --cal-frames 60` all checks pass. |
| 45 | Results figure: rBFi + ⟨I⟩ + parameters box (U5, 2026-10-10) | Protocol step 6g: "a figure with two axes: rBfi vs time and <I> vs time. Add text box with all parameters used." `rBfi_fig.png` used to be a screenshot of the live plot (pyqtgraph), rBFi only. It is now drawn **from the results file** by the new `core/results_figure.py` (matplotlib, its Agg canvas only — never `pyplot`, which would pick a Qt backend inside the app and the tests): **rBFi on top, ⟨I⟩ below, two separate plots, each with its own x and y axis** (the user's choice, 2026-10-10; the todo row had said side by side; Vika's MATLAB rBFi figure, `Ver2.m:517-536`, also stacks them), and a **parameters box** on the right. Why from the file: the live plot has neither ⟨I⟩ nor the NaN rows (κ² ≤ 0), and the file's `rBFi` is the final one, so the figure can never disagree with the data beside it; NaN rows show as gaps. Copied from the MATLAB figure: minutes over 120 s (`NORM_LONG_RECORDING_S`), seconds otherwise; rBFi y-axis `[0, min(10, max)]`; ⟨I⟩ axis `<I> [DU]`. The box shows only what the files record: start time, duration, frames (and how many had κ² ≤ 0), camera model and SN, pixel format, exposure, gain, G and its source, frame rate, window size, ROI, dark/bright frame counts (from `Calibration.h5`; "—" if that file is missing), normalization method, window and constant, time source, frames lost/dropped, code version. Title = the session folder's name. 1600 × 900 px. Drawn at FINISHED through the recorder's still-open handle (new read-only `HDF5Recorder.file`) — opening the same path again while it is open for writing can fail on HDF5's file lock. A failure is still logged and swallowed; no rBFi → no figure, as before. `PlotWidget.save_png()` removed (nothing else used it). Long recordings: the line is drawn in chunks (`agg.path.chunksize`) — measured on the rig PC, 576 000 points 7.5 s → 1.4 s, 1 152 000 points 7.7 s → 1.2 s, on the GUI thread at FINISHED. Checked on the 2026-10-08 rig session: the pulse is visible in both plots; the box reads SN 40075248, G 0.9564 (table), 600 + 600 calibration frames, 0 lost. **Not shown, because no file records them:** trigger mode and delay, the recording name as typed (the folder name carries it). The GUI's normalization type was on this list too — now saved, Done item 46. Tests: `tests/test_figure_export.py` rewritten (12 new figure tests — plotted data equals the file's `rBFi` with NaNs and `Intensity`, stacked with independent axes, minutes/seconds, y-range, box contents and text, "—" for missing calibration, 1600 × 900, no rBFi → no file, 216 000 points; one session test rewritten to check the figure built at FINISHED against the saved file). Mutation-checked: raw `bfi` plotted, NaNs dropped, shared x axis, no minutes switch, no y-range, calibration counts lost → each caught; chunking off is not caught (it only changes speed). Fast suite 486/486; `rehearsal.py` normal all checks pass. |
| 46 | The normalization type used is saved in the results file (2026-10-10) | **Question** (asked after U5): three settings used in a session are saved nowhere — trigger mode and delay, the GUI's "Norm. type", the recording name as typed — so the figure's parameters box cannot show them; add them to `metadata`? **User's decision:** save the normalization type that was actually used; nothing was decided about the other two, which stay unsaved. **Why it matters:** `Params.normalizationMethod` records only the statistic (`mean` / `percentile5`), and a recording ≤ 120 s ends on `percentile5` under either type, so the file could not say whether the operator chose "Pulsation lower level" or the short length chose it. **What:** `metadata.normalization_type` = the GUI's wording, `Number of seconds` or `Pulsation lower level` (`NORM_TYPE_LABELS` in `gui/main_window.py`, also used to fill the combo box, so the file and the screen cannot drift apart). Written in `MainWindow._write_rbfi()` right after `rBFi`, i.e. only when a normalization actually happened — no rBFi, no type. "Actually used": the combo box is locked from Start SCOS to the end, so the type at the end is the one the run used, including a switch made by the "Short Recording Detected" prompt before the start. **metadata, not Params:** Params is exactly the supervisor's ten fields (`tests/test_results_schema.py`). Shown in the figure's box as "Norm. type"; `tools/rehearsal.py` checks it against the GUI. Tests: `TestResultsFileRecordsTheNormType` in `tests/test_normalization.py` (4 — each type saved, not in Params, wording matches the combo box, absent without rBFi); mutation (always "Number of seconds") → 1 fails. Told to Vika in `docs/expectations.md`. |
| 47 | "Save Data..." button removed (U8, 2026-10-11) | Protocol Notes: "Remove the 'Save Data' button (data is always saved)." The button exported the plotted series to `.mat`/`.npz` (`scosTime`, `scosData` = κ², `rBFi`, …) after a session; every session already writes `rBfi_results.h5` on its own. Removed: the button, `MainWindow._save_data()`, its connection and the three lines that enabled/disabled it. **Asked first (user):** should the button be reused for raw frames (F1)? No — raw frames must be written *while* measuring, and the button only worked after the session, when the frames are gone (keeping them in memory would be ≈ 5.6 GB per minute at 1216 × 1936, 20 Hz). The "Save Frames" check box, already wired to `HDF5Recorder.append_frame()` and greyed out until F1, is that control; it now sits alone in its row. **Tests:** the two `_save_data()` tests in `test_normalization.py` (κ² is not 1/rBFi; time in seconds) moved to the results file (`TestResultsFileValues`: `k2_corr`, `rBFi` = 1/(κ²·constant), `timeVec` in seconds); `test_calibration_exits.py` no longer checks the button; `tests/test_save_load.py` deleted — it only round-tripped scipy/numpy files with the export's key names and never called app code. CLAUDE.md's export line removed; the note in `SCOS_protocol.md` updated. |
| 48 | Results file renamed to the protocol's name (U9, 2026-10-11) | Protocol step 6: "Save into .h5 file 'rBFi_resultsAndParameters'"; the app wrote `rBfi_results.h5`, the name in Vika's older `docs/session_tab`. The user chose the protocol's name on 2026-10-08. New constant `core.recorder.RESULTS_FILENAME = "rBFi_resultsAndParameters.h5"` next to `CALIBRATION_FILENAME`, so the name is written once; `MainWindow._start_recorder()` and the tests use it. `tests/test_results_schema.py::TestFileName` spells it out, because every other test reaches the file through the constant and would pass with any name. `tools/rehearsal.py` also spells it out (`RESULTS_FILE`) as an independent check. The figure keeps `rBfi_fig.png` (U9 did not cover it). **Old sessions** keep `rBfi_results.h5`: nothing in the app opens a results file by name (`--mock-h5` takes a path), so they still open; anything added later that looks for the file inside a session folder must accept both names (noted at the constant). Docs updated where they describe the app as it is now or tell someone what to look for (CLAUDE.md, `SCOS_protocol.md` note, `expectations.md`, `rig_checklist.md` R6d/R6e, `simulation_checklist.md` C2/C4/F5, `open_questions.md` drafts, this file's E2/D5/F5); records of past sessions and Done items keep the old name, which is what those files are called. `reviews/merged_worklist.md` (frozen) untouched. |
| 49 | Camera-side crop on "Cut Image" (U6, 2026-10-11) | Decided with Vika 2026-10-08. "Cut Image" used to zoom the display only (a square around the circle + 20 px); the camera kept sending the full 1216 × 1936 sensor and processing used it all. Now the **camera itself** sends only the ROI circle + `CROP_MARGIN_PX` = 26 px (= 51 // 2 + 1, the largest window's reach, so no filter loses a neighbour; 20 px failed at window 51), rounded to the camera's own steps — for display, both calibrations and the measurement. Seven steps ([`Plan_U6_Camera_Crop.md`](Plan_U6_Camera_Crop.md) §6, each with its numbers): (1) `core/crop.py` — `crop_for_circle`, `circle_mask`, `Crop`; (2) **accuracy proof** on the lab recording, `tests/test_crop_accuracy_offline.py` (slow): κ²_raw, κ²_corr, ⟨I⟩ and spVar **identical** with and without the crop, dark_var within 9 × 10⁻¹⁵; (3) `camera.py` — `set_roi`/`clear_roi` return the crop **read back** from the camera (`applied_crop`), a refused crop falls back to the full sensor with a warning, every restart re-applies it (test added in step 7: trigger switch and pixel format keep the crop); checked on the pylon emulator; (4) playback sources really crop (`core.crop.PlaybackCrop`); (5) GUI — the mask follows the read-back crop, the circle stays in sensor coordinates, old-size frames still queued are **skipped** (they used to reset the ROI to the whole frame), Cut/Full and the ROI locked from DARK_CAL until FINISHED; (6) `tools/rehearsal.py --cut [--cut-radius R]` with new checks in every run (calibration arrays and mask have the frames' size at MEASURING) — all scenarios pass, three mutations caught; (7) docs: CLAUDE.md (new IMPORTANT note), `rig_checklist.md` R2g/R2h/R6f, `simulation_checklist.md` section H. Decisions D1–D4 (plan §7, user 2026-10-11): circle locked while cut; locked from Start SCOS; `Params.ROI` in sensor coordinates; playback crops too. **Not yet run on the real camera** — rig check open. Follow-ups: U10 (crop in the results file), B5 (empty ROI mask) |
| 50 | The camera's image area saved with the results (U10, 2026-10-11) | Vika, 2026-10-08: record the crop used — width, height, OffsetX, OffsetY and the full sensor size — "as a struct" in `metadata`. Now `metadata/crop`, a subgroup of attributes named after the camera's own features: `OffsetX`, `OffsetY`, `Width`, `Height`, `SensorWidth`, `SensorHeight` (MATLAB: `h5readatt(file, '/metadata/crop', 'OffsetX')`). The same subgroup in `Calibration.h5`'s `dark` and `bright` groups (inside them, not a third top-level group). The values are the crop the camera **reported** (`MainWindow._session_crop()` → `_camera_crop`), locked from DARK_CAL, so one crop for both calibrations and the measurement; uncut = the full sensor; a source that cannot crop records a full frame of the mask's size. `Params` unchanged (still her ten fields; `Params.ROI` stays in sensor coordinates — plan decision D3). The figure's parameters box gained "Image area" (`354 × 354 at (1039, 508)` / `full sensor`) and "Sensor" — one long line ran past the figure's edge, so two rows. `core/recorder.py`: `CROP_GROUP`, `crop_attrs()`, `write_crop()`; `crop=` on `HDF5Recorder` and `write_calibration`. Tests: `TestCropRecord` in `tests/test_results_schema.py` (8 — includes the real dark → bright → results path on a cut crop with offsets, width and height all different); 3 deliberate mistakes each caught (Width/Height swapped, bright group written without it, the requested-not-reported crop replaced by the mask's full frame). Rehearsal: new `check_crop_record` in **every** run (the three `crop` groups = the crop at MEASURING; arrays are Height × Width; uncut = full sensor, `--cut` = the clicked crop) — `normal`, `--cut` (1652 × 1216 at (284, 0)) and `--cut --cut-radius 150` (354 × 354 at (1039, 508)) pass; new mutation `crop-xy` (offsets swapped) fails all three. **To tell Vika** (plan D3): `Params.ROI` (cx, cy, r) stays in full-sensor coordinates in a cut session too; subtract `OffsetX`/`OffsetY` to place it in the cut arrays |
| 51 | Faster dark-calibration collector (D8, 2026-10-11) | `DarkCalCollector.add_frame()` made four or five new full-frame float64 arrays (19 MB each at 1216 × 1936) for every frame. It now writes every intermediate of the same Welford update into two scratch arrays allocated with the mean on the first frame — the same operations in the same order (division by n kept: multiplying by 1/n changes the last bits), so the results are **bit-for-bit identical**; the caller's frame is never written to. **Measured** (dev PC, 1216 × 1936 uint16, alternating rounds): collector alone **30.7 → 15.0 ms per frame (2.05×)**, outputs `array_equal`. In the app (600-frame `normal` rehearsal, 40 Hz playback, one run each): dark calibration **36.6 → 19.6 s** (15 s is the camera's minimum), backlog flushed after it **559 → 31 frames**, whole rehearsal 114 → 84 s; bright calibration unchanged (≈ 21 s — `BrightCalCollector` not touched). These before numbers are well below the 2026-10-06 table (89 ms / 68 s) for the same collector code — cause not investigated (the app around it has changed since). Tests in `tests/test_session.py`: bit-identical to a verbatim copy of the old `add_frame` for uint16, a 354 × 361 crop, uint8, float64 and float32 (`array_equal`, not `allclose`); frame never modified (uint16 and float64); two collectors independent. Two deliberate mistakes caught (×1/n instead of ÷n: 5 fail; the frame used as scratch: 2 fail). Slow suite 6/6 (incl. `test_dark_cal_offline.py`). Unblocks **U11** |
| 52 | Dark calibration at the camera's maximum frame rate (U11, 2026-10-12) | Vika, 2026-10-08: the frame rate does not matter for dark frames, so during DARK_CAL the camera's frame-rate limit is switched off. Plan with decisions D1 (skip surplus dark frames in the GUI), D2 (record the rate), D3 (no GUI switch): [`Plan_U11_Dark_Cal_Max_Rate.md`](Plan_U11_Dark_Cal_Max_Rate.md), each step's numbers in §6. **Checks first (step 1):** on the pylon emulator `AcquisitionFrameRateEnable` can be written while grabbing (20 → 58.5 → 20.7 fps, exposure unchanged); the emulator has no resulting-rate node, so that value is optional; the USB throughput limit is On (360 MB/s → 76 fps for full Mono12). `tools/bench_dark_collector.py --handler`: the GUI takes ≈ 58 full frames/s, ≈ 635 cut. **Code:** `camera.py` `set_max_frame_rate()` (the restore writes the limit *and* the rate, any trigger mode, grabbing or not; restarts keep the maximum; log line with the read-back exposure, gain, format, crop and USB limit); both playback sources (`core.crop.playback_max_rate()`: 1 / exposure capped by USB bandwidth); `MainWindow` — on in `_start_dark_cal`, back at every exit and **before** the trigger is restored, surplus dark frames dropped in `_on_scos_frame` (counted from the smallest backlog seen, so a counter offset cannot stall it), `Calibration.h5` dark `duration_s` + `camera_frame_rate_hz`. **Tests:** `TestMaxFrameRate` (14), playback (5 + 4), `tests/test_dark_max_rate.py` (18), `tests/test_dark_rate_ab.py` (6); 15 deliberate mistakes across camera and window each caught. **Rehearsal:** `check_dark_rate` in every run (at maximum during DARK_CAL, set rate back for everything after, queue does not grow, file record); `normal`, `--cut`, `slowdown`, 600 frames pass; mutations `dark-no-restore`, `dark-no-skip` caught. **Found:** in playback the maximum rate does not speed the dark calibration up — the window is the limit and a faster playback thread competes with it (600 frames: 38.9 s vs 32.4 s at the set rate, back to back). **Proof still open:** `tools/dark_rate_ab.py` on the rig (20 Hz → max → 20 Hz, lens capped; PASS = the maximum within the two 20 Hz sets' difference) — `rig_checklist.md` R6g–R6i |
| 53 | The measurement waits for the camera-clock check (D10, 2026-10-14) | After short calibrations (60 + 60 frames) a measurement could start inside the 5 s check that follows every camera restart — the dark calibration's trigger switch is one — so `timeVec` began on PC stamps and continued on camera stamps, with a few-ms step between, and `time_source` said `camera`. User's decision (option b): `RealtimePipeline.on_frame()` (camera thread, right after the frame was stamped) lets frames pass without latching t = 0 while `CameraThread.clock_settled` is False — `FrameClock.settled`: the clock accepted or given up on for good. Once t = 0 is latched it is never asked again. One `app.log` line: "Measurement starts after the camera-clock check — N frame(s) captured during the check were not used". The wait is at most ≈ 5 s, only on the real camera; playback sources have `clock_settled = True`, a source without it never waits (`MainWindow._enable_measurement_intake`). **Also:** the camera's lost-frame count is now taken at t = 0 (`frames_lost_at_t0`), not when the intake opens, so frames lost while waiting are not charged to `frames_lost_camera`. **Tests:** `TestSettled` (5, `tests/test_frame_clock.py`), `TestClockCheckGate` (6, `tests/test_pipeline.py`), `TestMeasurementWaitsForTheClockCheck` (3, `tests/test_camera.py` — the real grab loop into the real intake, jittery retrieval: timeVec exactly 0.05 s apart from its first point, ≈ 100 frames skipped; without the wait the two clocks mix), 2 window tests in `tests/test_results_schema.py`. Seven deliberate mistakes each caught (no wait; `settled` always True; give-up not settled; window passes no wait; wait asked after t = 0; lost count from the intake opening — in the pipeline and in the window). Not provable in rehearsals or on the pylon emulator: neither has camera timestamps. **`startTime` fixed with it:** it was the time the results file was opened, before t = 0 — normally ≈ 1 frame earlier, but up to ≈ 5 s earlier once the measurement waits. Now `MainWindow._write_start_time()` rewrites it at the end from the pipeline's `t0_wall` (latched with the first kept frame; `HDF5Recorder.set_start_time`, same fixed-length ASCII); a session stopped during the wait keeps the opening time. Tests: recorder rewrite, window uses t = 0, Stop during the wait writes an empty file without errors; dropping the call is caught. **Found on the way:** the **V** key could switch the trigger mid-run — removed, Done item 54 |
| 54 | The V keyboard shortcut removed (2026-10-15) | Found during D10 (Done item 53). **V** toggled External Trigger (`MainWindow.keyPressEvent`, added 2026-03-30 with the Arduino trigger, commit `5da3d7d` — a development convenience, never documented). It called `setChecked()`, which Qt allows on a **disabled** checkbox (checked in Qt, and by the new test before the fix), so V worked during a measurement although the panel is locked: the trigger switched, the camera restarted mid-run, `timeVec` got ≈ 5 s of PC stamps inside the run, and with the trigger On → Off the camera left the Arduino's pulses for its own rate — nothing in the results file would show it. Options offered: guard V while the panel is locked, or remove it. **User's decision: remove it** — Vika's MATLAB GUI has no keyboard shortcuts (`SCOS_GUI.m` has no key callbacks; every `KeyPressFcn` in `SCOS_GUI.fig` is empty) and the operator does not know V exists; the checkbox still switches the trigger. A search found no other shortcut in the app (no `QShortcut`, no Alt+letter, pyqtgraph widgets without key bindings). Left as standard Qt behaviour: **Space** presses a focused button — during a measurement only Start/Stop SCOS is enabled, and stopping saves everything as usual. **Test:** `tests/test_no_hotkeys.py` — V and Shift+V leave the trigger and the Arduino alone, in preview and with the panel locked; the window defines no key handler of its own (a new shortcut must change that test) |
| 55 | Rig-trip sheet for U6/U10/U11 + `tools/check_session.py` (2026-10-15) | The first rig session (2026-10-08) ran before the camera-side crop (U6), its record (U10) and the dark calibration at the maximum rate (U11) existed, so none of them has run on the real camera. [`rig_trip_U6_U10_U11.md`](rig_trip_U6_U10_U11.md) is a short sheet with just those checks in order: A before launching, B a cut session with 600 + 600 frames (U6 + U10 + U11 together; expected crop for the 2026-10-08 ROI computed with `core.crop.crop_for_circle`: 611 × 611 for camera steps of 1, 612 for 4, 616 for 8), C the same uncut, D the A/B accuracy test with the lens capped (U11 is not done until it passes), E optional (a 60 + 60 session for D10, leftovers R1f/R3d/R7). **`tools/check_session.py <session folder>`** reads the results file, `Calibration.h5` and the session's `app.log` and prints each value with a verdict: **OK / NOT OK only for hard rules** (time steps = 1000 / frame rate with no gap or bunching and the first step too; no lost/dropped frames; κ² > 0; one crop for results, dark and bright, arrays of its size; dark faster than at the set rate and the camera's rate above it; **exposure and gain in the "Frame-rate limit off" line = the session's**), **CHECK** where no hard rule exists (κ² mean, `mean_dark` — black level differs by camera, estimates, 60-frame tests, PC clock), a `Summary` line, exit 1 on any NOT OK. It only reads. Written because a Python one-liner with nested quotes broke when tried through a Windows shell. **Checked:** on the real 2026-10-08 rig session — time steps OK (50.01–50.02 ms), everything added later CHECK, the one NOT OK the deliberate laser-on test; that run found the accounting line "0 frame(s) lost" marked NOT OK, fixed by matching keys in order and case-sensitively. `tests/test_check_session.py` (14), nine deliberate mistakes in the rules each caught. Also: `rig_checklist.md` R3a no longer mentions the removed V key |
| 56 | The camera's BlackLevel recorded in the session files (2026-10-19, `04186c5`) | Found on the rig 2026-10-19: `check_session.py` showed `mean_dark` 112.7 DU (0.46 on 2026-10-08). Read on the camera: `BlackLevel` = **100** — a fixed offset the camera adds to every pixel, set outside the app; it is stored in UserSet1, the set the camera loads at power-on (confirmed by unplugging and replugging, `396179f`). The offset cancels in κ² (the dark mean is subtracted), but it hid what `mean_dark` is meant to show: light leaking in during the dark calibration (the other 12.7 DU — a leakier test probe that day). Now `camera.py` reads `BlackLevel` back after every `_apply_params()` (never writes it; `None` if the camera has no such node — playback and the mock), and the app records it as `camera_black_level` in the results file's `metadata` and in `Calibration.h5`'s `dark` and `bright` attributes; `check_session.py` prints `mean_dark - black level` (CHECK — no hard rule: −1.07 DU at 2 ms exposure, +0.5 to +2.9 DU at 8 ms on 2026-10-19, so "near 0" means about ±1 DU plus any leak). Old sessions print the old row unchanged. Tests: `tests/test_camera.py` (read back through a read-only node, missing node → None), `tests/test_dark_max_rate.py` (dark → bright → results file, 100.0 and None), `tests/test_check_session.py` (row with and without a level); six deliberate mistakes each caught (one only after the test was tightened: "12.700 DU" is a substring of "112.700 DU"). Rig ✅: `camera_black_level = 100` in every session from 16:11 on. Also measured: the room's 100 Hz flicker raises the dark variance at the maximum rate (U11) more than at 20 Hz when light leaks in — negligible with the usual probe |
| 57 | Saved duration and normalization take effect at launch (2026-10-19, `5d9767e`) | Found on the rig 2026-10-19: with "Measuring duration" saved as 1 min the run went on until Stop SCOS (138 s and 80 s), and a saved "Pulsation lower level" showed in the dropdown while the run was normalized by "Number of seconds" (both results files record that). Cause: `MainWindow.__init__` ran `_load_config()` before `_connect_signals()`, so the handlers that copy three widgets into the values the run uses never saw the loaded values: `_measurement_duration_s` stayed ∞, `_norm_type` "seconds" (and the seconds box visible), `_norm_seconds` 5. In the code since May 2026 (`09513cf`, `9baad98`); a value changed by hand during a run worked, which is why the real 2026-10-08 session is correct (its log shows both set in-session, the auto-stop fired, percentile5). Effect on data: only the rBFi normalization of sessions whose type came from the saved file; κ²_raw, κ²_corr and BFi unaffected. Fix: `_apply_loaded_settings()` right after `_load_config()` sets the three values from their widgets — not through `_on_duration_changed()`, which may ask "Short Recording Detected" at every launch. The camera settings, window size and frame counts were already read from their widgets when used; a loaded External Trigger tick still does not upload the Arduino sketch (not changed). Tests: `tests/test_settings_persistence.py` (2 new — the round-trip test checked the widgets only, so it passed throughout); removing any one of the three lines is caught. Rig ✅ 2026-10-19 20:01: "Measurement duration reached (1.0 min) — auto-stopping", normalized by percentile5, `normalization_type` "Pulsation lower level" |
| 58 | Save Frames — raw frames for rerunning the algorithm (F1 with B2 and the frame part of F4, 2026-10-21) | Plan [`Plan_Frame_Saving.md`](Plan_Frame_Saving.md); decisions 1–4 as recommended (user, 2026-10-20). Format measured on the rig PC: uncompressed TIFF, 2.7 ms and 4.71 MB per full frame, sustained ≈ 960 MB/s ≈ 200 full frames/s (zlib 7× slower for half the size). `core/frame_writer.py` (own thread, 40-frame queue, skip and count, never waits; `Frames/<expT…_Gain…_FR…_BL…DU>` + `_dark` + `_bright` + `frame_times.csv`; file times = capture times, because the MATLAB script sorts by them; six-digit index; no timestamp in the names and a refusal when a path would pass Windows' 260 characters — a deep folder had lost every bright frame), `core/disk_space.py`, the pipeline's `frame_sink` (measurement frames from t = 0, on the camera thread), `MainWindow` (checkbox enabled, locked during a run, not remembered; free-space check before the folder is made; writer opened after the laser prompt; closed on every way out, before the `_cancelled` rename; counts in `metadata`; disk-full stop). Playback replays a saved session: settings and BL from the folder name, pixel scale from the frames (×64 / ×16 / ×1) instead of a fixed 64. **Found in her code:** BL in the folder name must be the real black level — `FitMeanIm` subtracts it, and a BL0DU playback save gave her κ²_raw ≈ 2800. Checks: `tests/test_frame_writer.py` (22), `test_disk_space.py` (11), `test_save_frames.py` (21), pipeline (+2), playback (+8), `test_compare_with_matlab.py` (4); 17 planted mistakes each caught; `tools/rehearsal.py --save-frames` (600 + 600, cut) passes — every timeVec point has its file, the saved pixels are the source frames; the saved session replays. Her script (baseFunc `main`) on a saved session: 1250/1250 frames, same order, raw κ² within 0.05 %, ⟨I⟩ 0.09 %; the app's rerun on the saved files bit-identical to the results file; with her G and spVar, corrected κ² within 0.10 % (weak there: 10-bit data read as 12-bit, G 4× too large). New `tools/compare_with_matlab.py`. Rig check: R8 |
| 59 | `tools/check_saved_frames.py` — check a Save Frames session before it goes to Vika (2026-10-25) | The rig check of 2026-10-22 (`rig_checklist.md` R8, session `scos_20261022_165057`) was done with a scratch script; now a tool in `check_session.py`'s style (OK / NOT OK / CHECK rows, exit 0/1/2, read only). Checks: file counts against `metadata` (`frames_saved_*`, `frames_not_saved_*`) and the calibrations' `n_frames`; the BL in the folder name = `camera_black_level` (her `FitMeanIm` subtracts it); file names carry the SN between `__`; 16-bit (8-bit for Mono8) uncompressed; file times rise (she sorts by them); longest path ≤ 259; `frame_times.csv` one row per file and a saved file for every `timeVec` point (≤ 20 + dropped extra, the queue at Stop SCOS); the dark/bright means rebuilt from the files (summed frame by frame — a stack of 600 full frames would be 11 GB) vs `Calibration.h5` within 10⁻³ DU; κ² recomputed by `SCOSProcessor` on every saved measurement frame vs the results file within 10⁻⁶ relative (pixel scale from `FolderMockCamera._pixel_layout`). On the rig session: 20 OK, 0 NOT OK, 77 s (dark 3.8 × 10⁻⁶ DU, bright 5.7 × 10⁻⁶ DU, κ² identical). Tests: `tests/test_check_saved_frames.py` (9 — a good session built with the app's own writers passes everything; a missing file, a changed measurement or dark frame, a wrong BL, file times swapped, skipped frames, a compressed file, a folder without frames each fail the right row); 7 planted mistakes in the tool each caught. Found while testing: a missing file crashed the recomputation — such rows are now left out and counted |
| 60 | Arduino program sent at launch + frame-timing warning (2026-10-25) | Rig finding 2026-10-22 (the F3 note "a stale Arduino program"): opened with External Trigger already ticked, the app sent no program and the Arduino ran someone else's — frames every 100 ms while the results said 20 Hz. **User's decision: both fixes.** (1) `MainWindow._upload_arduino_at_launch()` sends the program when the tick is restored — real camera only (`_has_real_camera()`, shared with `_save_config()`); a failure unticks the box with the usual "Arduino Upload" message, so the next launch starts unticked. While a program is on its way (`_arduino_pending()`: upload running, or the 1 s wait after an exposure/FPS change) **Start Video runs the camera untriggered** (a triggered camera without pulses gives up after 10 s; `_on_arduino_done` switches the trigger on as for a tick by hand), a Trigger Delay change keeps it untriggered, and **Start SCOS is refused** ("Arduino Not Ready") — the trigger switching on later would restart the camera in the middle of the dark calibration. (2) `_check_frame_timing()` in `_on_scos_frame`: the **median** time between capture times over 3 s against the Frame Rate field (the value `Params.frameRate` and the frames folder name record), tolerance **5 %** — the Arduino times in whole milliseconds, up to 4.8 % off below 118 Hz, 0 % at 20 Hz. Catches what the lost-frame count cannot see: a skipped trigger pulse never becomes a frame. Not in DARK_CAL (maximum rate on purpose), not in playback, not while a program is on its way. Starts over on every state change, exposure/FPS/format/trigger/delay change, Cut/Full Image and Arduino answer, and ignores frames captured before that + 1 s — older frames still wait in Qt's queue (capture times are monotonic, so this holds on either clock). Warning: a non-modal "Frame Rate Mismatch" window (one, updated — the run and the frames go on behind it) with the likely causes for trigger on / off and for too slow / too fast (too slow also names frames lost between camera and PC, which show as "Camera: N frame(s) lost" — without that message the frames were never taken), the status bar and `app.log`; once per cause — a state change does not re-arm it (else a wrong rate lasting through a run would reopen the window in every phase, over the laser prompts); a settings change, the Arduino's answer, Cut/Full Image or a recovered rate ("Frame timing back to the set rate") do; never shown while a modal dialog is up (it comes with the next frame after it closes); `app.log` also gets one "Frame timing OK: … Hz measured" per phase. **Not added:** a measured rate in the results file (`timeVec` already holds it) — offered, not decided. Checked: the overload test of 2026-10-19 had FPS 150 typed in after Start Video, which is why its file said 150 (set before Start Video, `_sync_params_from_camera()` reads the free-running camera's reachable rate back into the field — from the code; not tried on the rig, the pylon emulator has no resulting-rate node). If the box is unticked while the program is on its way, the Arduino's answer leaves the trigger off. **Not handled (was so before):** closing the app while a program is being sent — `closeEvent` does not wait for the upload thread. Tests: `tests/test_frame_timing.py` (24); 18 planted mistakes each caught; fast suite 802; rehearsal normal passes. Rig check: `rig_checklist.md` R9 |
| 61 | Laser-off check before the dark calibration (2026-10-26) | Rig finding 2026-10-22: OK clicked at "Please turn off the laser" with the laser on — no warning, the picture flickered (the camera untriggered at its maximum rate while the Arduino keeps flashing the laser), the run stopped by hand. **User's decision: check, and stop the run when it fails.** **Her decisions 2026-10-27 on what was built beyond the approved plan** (she asked what it meant; my recommendations taken): the fixed 5 DU limit when the live picture was already dark is **kept** (the approved plan said "skip") — a steady leak would be subtracted correctly, but room-light flicker inflates `var_dark` and a leak that changes during the session biases ⟨I⟩; whether a leaking probe should stop or only warn is **open question 21, sent to Vika 2026-10-27** — until she answers, it stops; the 2 DU floor kept; the red window's wording approved. `core/dark_light.py` + `MainWindow._prepare_dark_light_check()` / `_watch_dark_light()` / `_check_dark_mean_light()` / `_stop_dark_cal_for_light()`. Light = mean inside the ROI in DU **minus the camera's black level** (no dark calibration exists yet, so the reported offset is the only zero; `None` → skipped and recorded, never guessed). Units: Basler's black-level page — on most ace 2 USB models +1 BlackLevel = +1 grey value; ours show it in the current pixel format's DU (10-bit lab recording BL100 → dark 99.3; rig Mono12 BL100 → 100.5). **Reference:** the live picture's ⟨I⟩ in PREVIEW over the last 2 s, read **before** the laser prompt opens (the prompt keeps delivering frames, dark once the laser is off); dropped when the ROI changes or is older than 2 s. **Limit:** a 90 % drop, the end-of-measurement check's rule, but never under 2 DU (the offset is known to ≈ ±1 DU); without a reference (live picture under 5 DU above the black level, or none) a fixed 5 DU. **Two checks:** (1) each of the first dark frames on its own for ≥ 1 s and ≥ 10 frames — the brightest counts, because with flicker only some frames are lit; frames captured before OK + 0.2 s are ignored (by capture time, on top of the count-based flush; with 60 dark frames at the maximum rate the step is over in ≈ 0.5–0.8 s, so check (2) alone judges it); (2) the finished `dark_mean` before anything is stored or written (a laser switched on part-way). **Failure:** `_abandon_calibration()` first (rate, trigger, folder removed or `_cancelled`), then the red window "Dark Calibration Stopped — Light on the Camera" (`safety_dialog.show_dark_not_dark`, one OK). `Calibration.h5` `dark` attributes `light_check` ("passed" / "skipped - no black level"), `light_check_basis`, `light_check_limit_du`, `light_check_mean_du`, `light_check_reference_du`, `light_check_first_frames_max_du`. Rig numbers for scale: laser on 33 DU (2026-10-22) and 114 DU (2026-10-08) above the black level; normal probe dark ≈ 0.5 DU; the leaky test probe's room light, 12.7 DU, **also stops the run** — intended, it made κ²_corr 1–3.5 % low. Tests: `tests/test_dark_light_check.py` (20; 15 planted mistakes each caught); `test_playback_calibration.py` fixture now sets the ×64 pixel scale the playback auto-load sets. Rehearsal: normal passes (reference 21.2 DU, limit 2.1 DU, dark −0.70 DU), new check on the `light_check` attributes; it now plays 2 s of live picture before Start SCOS (the load places the ROI circle the moment Start SCOS is enabled); new mutation `laser-on` → the run is stopped 0.6 s into the dark calibration, exit 1. `--cut`, `slowdown`, `recovery` pass too (light check against the live picture in each). **Found on the way, not caused by this:** `--save-frames` fails "saved measurement frames without a result: 26–27 (at most the 20-frame queue + dropped)" — the same on `76f218b` without this change (2026-10-26); the check's limit was too low — see item 62. Rig check: `rig_checklist.md` R10 |
| 62 | `--save-frames` rehearsal: "saved measurement frames without a result: 26–27 > 20" (2026-10-27) | **Not lost data — the check's limit was too low.** Measured with temporary logging at Stop SCOS: queue 20 + 5–6 with the workers, and exactly 26 results arrived after FINISHED and were not used = the 26 extra frames; all of them one unbroken block at the end (frames 313–338 after the last result), none in the middle. The limit counted only the 20-frame queue, not the frames handed to the 3 workers (`_inflight_sem` = 2 × workers) nor the one the dispatcher and the camera thread each hold while waiting. **Not a regression:** `d949741`, where it passed on 2026-10-21, fails the same way today (24) — the run is saturated ("SCOS overload — input queue 16/20") and how full the queue is at Stop depends on the PC's load. **Headless only:** on the rig the "Measurement Ended" prompt is a real window, and the workers finish the queue while it is open — the rig session of 2026-10-22 had 1301 frames and 1300 results (the one left is the result that triggers the auto-stop). **User's decision: fix the checks, not the app** (the other option was to process the tail at Stop SCOS); offline tool uses the worst case. `core/pipeline.py`: `INPUT_QUEUE_SIZE`, `MAX_WORKERS` (now the spinbox's maximum), `RealtimePipeline.max_frames_held` = queue + 2 × workers + 2, `WORST_CASE_FRAMES_HELD` (8 workers: 38). Both checks now split it: **mid-run** frames without a result must be ≤ `frames_dropped_queue` (NOT OK otherwise — the files would no longer line up with the results); **at the end** ≤ `max_frames_held` + 1 (rehearsal, 29 with 3 workers) / `WORST_CASE_FRAMES_HELD` + 1 = 39 (`tools/check_saved_frames.py`, which cannot know the worker count; CHECK above it). Tests: `test_pipeline.py::TestMaxFramesHeld` (stalled workers: exactly `max_frames_held` frames reach `frame_sink` before the camera thread blocks, 1 and 3 workers), `test_crop.py` (spinbox maximum = `MAX_WORKERS`), `test_check_saved_frames.py` (+4: a mid-run gap, the same gap with a dropped frame, a long tail, the limit); 3 planted mistakes each caught. Rehearsal `--save-frames` passes (tail 3); `--scenario slowdown --save-frames` passes: 5 dropped from the queue = 5 mid-run frames without a result, one for one, tail 25 of 29; `check_saved_frames.py` on it 21 OK, 0 NOT OK; fast suite 835 passed. |
| 63 | Save Frames easy to see (U12, 2026-10-27/28) | Rig, 2026-10-22: the tick was forgotten in two sessions running. **Final design — the user's own (2026-10-28):** just the **"Save Frames" checkbox, bigger** (14 px text, was the default size), inside a frame with a **coloured border** (amber `#d39e00`, the same ticked or not, no background fill); while it is ticked a small italic **"Frames will be saved"** appears beside it, and nothing when it is not. **Start SCOS keeps its plain text.** No size figure. **How it got there** (each version built, shown as a picture and run in playback): (1) 2026-10-27, her choice among four options — a filled amber/green strip with "Frames will NOT / WILL be saved — about X GB per minute" plus a Start SCOS label ("Start SCOS — frames NOT saved" / "+ save frames"); the options not taken were the strip only, the label only, and a question at every Start SCOS (one more click every session, and a new step among the protocol's opening dialogs); (2) the sentence made smaller and italic (in the same style it read as a second label for the box); (3) the Start SCOS label, the background fill and the checkbox's own text removed; (4) her final description above — the colour change, the "NOT saved" sentence and the GB-per-minute figure dropped too. Code: the checkbox's `toggled` shows/hides `lbl_save_frames`; nothing else changed in `MainWindow`. The tick is still **not remembered** between launches. Tests: `test_save_frames.py::TestEasyToSee` (2). Fast suite 837 passed; window still fits the screen. **Seen on the way, not caused by this:** one of the day's five full-frame `--save-frames` rehearsals (2.4 Mpx, 600 bright) had **15 bright frames not saved** — the writer's 40-frame queue was full, so it skipped and counted them as designed, and the rehearsal failed on the count; the rerun passed. A disk that stalls for ≈ 1 s on the full frame loses frames. Counted and shown, never silent, but full-frame Save Frames has not been run on the rig (`rig_checklist.md` R8 was cut). Rig check: `rig_checklist.md` R8e |
| 64 | A steady light leak warns, a flickering one stops (open question 21, 2026-10-28) | **Vika's answer (2026-10-28):** a leak that flickers during the dark calibration must stop the run; a steady one leaves the measurement valid, so only a warning — the operator decides. Until then any light above the limit stopped the run (item 61). **Built (user's decisions 2026-10-28, all as recommended, the strict setting):** three cases, `core/dark_light.py` `judge()` — **the laser** (light ≥ 50 % of the live picture's ⟨I⟩; a first dark frame that high stops at once, so the flashing laser is still caught within the first second) → stop; **flicker** — the per-frame ROI light's variance, less what pixel noise alone gives, would move κ²_corr by more than **1 × 10⁻⁵** (σ_f² / (reference − light)²) → stop; **steady leak** → the amber window "Light Leak During the Dark Calibration" (`safety_dialog.DarkLeakDialog`), **Stop** the default (Enter / Escape / the X / a window that cannot open all stop). Continue → `Calibration.h5` `dark/light_check = "steady leak - operator continued"` with the mean, the laser level, the frame std, σ_f², the κ² effect and its limit — the same record in the results file's `metadata` as `dark_light_check…` (every session, passed ones too). No live picture → still a stop. Light under the limit is never judged for flicker: the camera's own frame-to-frame jitter is in the measurement too. **Why judge the effect, not the flicker:** the same leak moves κ²_corr by ≈ 0.5 % at 114 DU of signal and by ≈ 27 % at 33 DU (2026-10-19 leak, σ_f² ≈ 0.5 DU² scaled from the rig's room-light test) — so that leak would still **stop** the run at either level; room light around a probe will usually stop it. **Measured for the limit:** saved dark frames of 2026-10-22 (good probe, 600 at the max rate): the camera's own jitter σ_f² ≈ 4 × 10⁻⁴ DU² → 4 × 10⁻⁷ at 33 DU, 1/25 of the limit; but frames 0, 3 and 4 were ≈ 1 DU brighter than the rest (the laser's last light just after OK?) and alone brought it to 4.9 × 10⁻⁶. The lab recording's dark folder: 4.6 × 10⁻⁷. Every collected dark frame gets one more ROI mean on the GUI thread: 1.6–3.7 ms on the full sensor (dev PC) — the full-image dark step a little slower, a cut image unaffected. Plan: [`Plan_Q21_Steady_Leak.md`](Plan_Q21_Steady_Leak.md). Frames arriving behind the amber window are dropped (`_dark_light_deciding`); a run stopped behind it (a camera error presses Stop Video) is not continued. Tests: `test_dark_light_check.py` (+22: `TestJudge`, `TestSteadyLeak`, `TestFlickeringLeak`), `test_laser_safety.py::TestDarkLeak` (+8); 10 deliberate mistakes in the code, each caught. Rehearsal: `--scenario leak` (a steady 6 DU on the dark folder — window once, both records, judged steady at 5 × 10⁻⁷) passes; `normal` passes; mutations `leak-flicker` (4 / 8 DU in turn → stop, κ² effect 1.5 × 10⁻²) and `leak-stops` both caught. `tools/preview_safety_windows.py` shows the new window. Rig check: `rig_checklist.md` R10a (wording), R10d (room light → F), R10e (a steady torch, optional) |

| 65 | The app starts from a desktop icon (D11, 2026-10-29) | The user asked for a double-click start instead of a terminal. **User's decisions (2026-10-29, left to me: "go with what you think is right"):** no console window, icon A (a cyan pulse curve, the live plot's colour, on the app's dark background; option B, a red laser spot with speckle grains, lost its detail at taskbar size). **Built:** (1) `tools/make_desktop_shortcut.py` — run once per PC; puts `SCOS.lnk` on the desktop (`GetFolderPath('Desktop')`, so a OneDrive desktop too): `venv\Scripts\pythonw.exe "<repo>\main.py"`, Start in = repo, icon `assets/scos.ico`. Made on the dev PC 2026-10-29. (2) `tools/make_icon.py` draws the icon at 16–256 px, each size on its own (line ≥ 2 px), into a PNG-in-ICO file; the `.ico` is committed. (3) `main.py`: `app.log` now goes **next to `main.py`** (`LOG_PATH`), not into the start folder — the only path that depended on it (settings, gain table and git hash were already anchored to their files; the output folder is picked in a dialog). Logging is no longer set up at import. (4) **One copy at a time:** a `QLockFile` (`scos.lock`, gitignored) is taken before `app.log` is opened — before this a second launch would have wiped the running session's log (mode="w") and fought it for the camera. The second copy shows "SCOS Is Already Running" and exits. Stale time 0: never released by age, only when the holder's process is gone (checked: held past 30 s, freed after a kill). Applies to the playback modes too. (5) **Crashes are no longer silent:** without a console an exception in a slot made the window vanish. `sys.excepthook` now logs the traceback (CRITICAL) and shows "SCOS Stopped — Error" (naming `app.log` and saying to copy it before the next start), shown by a separate tiny process so the app still **stops at once, as before** — carrying on in an unknown state could record wrong data. `threading.excepthook` only logs (a dying Python thread still ends only itself, as before). A crash inside C code (pypylon, Qt) bypasses both hooks: the window just vanishes, but `faulthandler` now writes every thread's stack into `app.log` (checked with a forced segfault). (6) `arduino-cli` and `git` run with `CREATE_NO_WINDOW` — under pythonw each would flash a black window. (7) Windows AppUserModelID set, so the taskbar shows the app's icon. **Checked on the dev PC through the real shortcut:** no console; `app.log` in the repo; the laser warning first (≈ 16 s after the click — imports); a second double-click gave the "already running" window and left the first copy's `app.log` intact; a forced error showed the crash window, exit code 1; a lock left by a killed copy was taken over by the next start (in a separate test).  Not checked: the taskbar icon by eye (left to the user). **Tests:** `tests/test_desktop_launch.py`. **Rig check:** `rig_checklist.md` R11. **Side finding, not fixed:** `confirm_laser_safety`'s docstring says `app.log` "is copied into the session folder" — nothing copies it; the next launch overwrites it |
| 66 | "Starting SCOS…" sign, and `app.log` copied into every session folder (2026-10-29) | Two follow-ups to the desktop icon (item 65), asked for by the user. **(1) The sign:** on a cold start the app's code takes ≈ 10 s to load (scipy, h5py, matplotlib — measured 2026-10-29; ≈ 1.4 s when warm), and with no console nothing showed meanwhile — an operator would double-click again. `main.py` now shows a small dark `QSplashScreen` (the icon, "Starting SCOS…", "This can take up to half a minute.") right after the one-copy lock is taken, and closes it **before** the laser-safety warning or any window opens — a splash stays on top and would cover the warning. Only PyQt6 and the standard library are imported at the top of `main.py` (pyqtgraph moved into `main()`), so the sign comes first. **Measured through the real icon, 2026-10-29:** sign after ≈ 1 s, laser warning after ≈ 2.4 s (warm). Twice the first start after a code change took ≈ 10 s before the first log line — before the sign could show (Python starting and loading freshly changed files; Windows scanning them is the likely cause, not proven). Expect that after each `git pull` on the rig. **(2) The log copy:** `app.log` is rewritten at every launch, and without a console it is the only record of a session (the laser-safety confirmation, calibration checks, warnings). `MainWindow._copy_app_log()` copies it into the session folder as `app.log` **after the probe-removal window**, so the copy covers the whole session (it starts at the app's launch, so it may hold earlier sessions of the same run too); also into a cancelled run's kept `_cancelled` folder (it says why the run stopped) — never into an empty one, which is still removed. The source is the root logger's file handler that writes a real file (pytest adds one on `nul`); in a rehearsal it is the rehearsal's log. A failed copy is only logged. `safety_dialog.confirm_laser_safety`'s docstring had claimed this copy since 2026-10-09 — now true. **Checked:** rehearsals `normal` and `leak` pass with `app.log` in the session folder, its copy ending with the probe-removal window. **Tests:** `tests/test_desktop_launch.py` (sign drawn, nothing heavy imported before it, shown after the lock and closed before the warning; a cancelled run's kept folder gets a copy, an empty one is still removed; copy holds the lines so far; placement after the last window; not into an empty cancelled folder) |
---

## Map: frozen `merged_worklist.md` tasks → this file

| Worklist | Here | | Worklist | Here |
|---|---|---|---|---|
| 1 in-flight cap | A3 / Done 7 ✅ | | 14 recorder thread | **F4** |
| 2 silent swallows | Done 17 ✅ | | 15 disk space | B2 |
| 3 ROI race | Done 18 ✅ | | 16 raw frames | F1 |
| 4 overload test | Done 19 ✅ | | 17 plot downsampling | D1 |
| 5 intake off GUI thread | Done 20 ✅ | | 18 fill bar + dialog | B1 |
| 6 verify on real camera | **D5** | | 19 camera reconnect | **B4** |
| 7 pool-vs-thread write-up | **D7** | | 20 long sessions | F2 |
| 8 session folder + FINISHED | Done 21 ✅ | | 21 Arduino laser | F3 |
| 9 results schema | E2 ✅ | | 22 float64 check | **A4** |
| 10 short/long normalization | E1 / Done 23 ✅ | | 23 protocol typo | ✅ `7c89dbc` |
| 11 laser popup + 90 % | E3 / Done 28 ✅ | | 24 scos_math | C1 |
| 12 figure | E4 / Done 27 ✅ | | 25 tooling | D2 |
| 13 tag v0 | E5 | | | |
