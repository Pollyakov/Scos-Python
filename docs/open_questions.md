# Open decisions pending the supervisor

Compiled 2026-09-07. Decisions that need the supervisor's input before the corresponding work
can be built correctly. Where work could not wait, it was built against the MATLAB reference
and the decision is marked provisional — each of those is a constant or a one-line change.

**Sources:** `docs/reviews/merged_worklist.md` (Questions Q2–Q6), `docs/Implementation_Plan.md`
§9 ("Open questions to resolve in Phase 1"), and ambiguities in `docs/session_tab` itself.

**Status 2026-09-23: questions 1–6 are all answered** — see "Answered" at the bottom. The
results-file schema (worklist task 9) is therefore unblocked, with one new ambiguity that the
answer to 4 created: see question 16. Questions 7–15 are wanted but not urgent.

**Status 2026-10-06: questions 7–16 were sent to the supervisor on 2026-10-04** (rig-session
prep step 0). Anything written after that goes under **"F. Still unsent"** until it is sent.

**Status 2026-10-07: she answered questions 7–16** (reply to the message of 2026-10-04).
Eight are settled and moved to "Answered" at the bottom. Two are still open:
**9**, because she replied with a question of her own, and **14**, because she asked for a
size estimate before setting any limits. Both are in section B below, with the follow-up
to send. One answer changes code that already exists: **11**. Up to now, stopping before the
normalization window ends wrote no `rBFi`. Now it has to normalize on whatever data exists
(todo rig-prep row 4c).

Worklist Q7 (the `_DropOldestQueue` sentinel eviction) is deliberately excluded — it is an
internal engineering decision, not a scientific or spec one.

---

## B. Still open after the answers of 2026-10-07 — follow-up to send

**9. If the laser-off check fails.** *Asked:* should a failed check block saving, or warn and
let the operator continue? *Implemented:* warn with "Continue anyway?". **No** does not
discard anything. It asks again to turn the laser off and repeats the check.
*Her reply (2026-10-07):* "What are the failure scenarios? Saving what?"

> **Draft answer to send.** *Saving what:* the results file `rBfi_results.h5`. By the time
> the check runs, the whole measurement is already recorded. `Calibration.h5` is complete.
> `timeVec`, `k2_raw`, `k2_corr`, raw `bfi` and `Intensity` are in the results file (written
> every 300 frames, about 15 s at 20 Hz; the rest is in memory and written at close).
> What is written **after** the check: `rBFi` and the normalization fields of `Params`, the
> frame counts in `metadata`, and `rBfi_fig.png`. "Blocking saving" would therefore mean
> throwing away a recording that has already happened. The check looks at one frame taken
> *after* the measurement, so it cannot say anything about the frames before it.
>
> *Failure scenarios*, i.e. the mean ROI intensity, dark-subtracted, has not fallen by 90 %
> compared with the last 5 s of the measurement:
> 1. The laser is still on. The operator clicked OK without switching it off, or the switch
>    did not work.
> 2. The laser is off, but other light reaches the sensor: room light, a monitor, a
>    door opened during the session.
> 3. The measured light was weak to begin with, for example a bad optical contact or a
>    moved probe, so a 90 % fall is lost in noise.
>
> Two more outcomes are not failures: the check is **skipped** (and says so) when no frame
> arrives within 2 s, or when there is no reference because the run stopped before any
> result.
>
> *Our question back:* what is the check meant to protect? (a) **Laser safety**, i.e. a
> reminder that the laser is really off. Then warning is enough, as it is now. (b) **Data
> quality**, i.e. evidence that the dark level at the end matches the dark calibration.
> Then we would also record the outcome — passed / failed / skipped, with both numbers — in
> the results file's `metadata`. Today it is only in the closing status message and
> `app.log`. Recording it there is cheap either way, and we would suggest doing it.

**14. Disk-space policy.** *Asked:* how much free space should be required to start, and at
what level should a running session stop cleanly? *Her reply (2026-10-07):* a check before
recording is a good idea. She asked for the space needed as a function of bit depth,
recording length, and whether frames are saved.

> **Estimate, computed 2026-10-07.** Raw frames are stored uncompressed, which is what a
> free-space check must assume. Speckle compresses poorly, and a check that underestimates
> is worse than none. Mono10 and Mono12 both arrive from the camera as 16-bit numbers
> (2 bytes per pixel), so they need the same space. Mono8 needs half. All sizes scale
> linearly with the frame rate. These are for **20 Hz**; at 40 Hz, double them.
>
> **Without saving frames**, a session writes very little. `rBfi_results.h5` holds 6 numbers
> per frame (`timeVec`, `k2_raw`, `k2_corr`, `bfi`, `Intensity`, `rBFi`): about 3.5 MB per
> hour, 14 MB over 4 h. `Calibration.h5` holds 5 frame-sized float32 images: at most 10 MB
> at 700 × 700. (47 MB at 1216 × 1936 uncompressed; 23 MB was measured with gzip on
> 2026-10-04.) The PNG is about 0.2 MB. That is **under 30 MB for any length** on the lab
> camera.
>
> **With every frame saved** (lab camera, 700 × 700):
>
> | Length | Mono8 (0.49 MB/frame) | Mono10 / Mono12 (0.98 MB/frame) |
> |---|---|---|
> | 1 min | 0.6 GB | 1.2 GB |
> | 10 min | 5.9 GB | 11.8 GB |
> | 30 min | 17.6 GB | 35.3 GB |
> | 1 h | 35.3 GB | 70.6 GB |
> | 4 h | 141 GB | 282 GB |
>
> If the calibration frames are saved as well (600 dark + 600 bright), add 0.6 GB (Mono8) or
> 1.2 GB (Mono10/12).
>
> For comparison, the a2A1920 (1216 × 1936, 4.7 MB per frame at Mono10/12) needs 5.7 GB per
> minute, 339 GB per hour.
>
> **Proposed defaults, for her to confirm or change:**
> - **Before Start SCOS:** require free space ≥ estimate × 1.2 + 5 GB. The estimate comes
>   from the frame size, bit depth, frame rate, the recording duration set in the GUI and the
>   Save Frames box. If the duration is "unlimited", use 4 h, the protocol's maximum.
>   Without frames this reduces to "at least 5 GB free".
> - **While running:** check every ~30 s. Stop the session cleanly (as if Stop SCOS were
>   pressed, so `rBFi` and the figure are still written) when free space falls below **2 GB**.
>
> These are proposals, not her decision. The margin and both limits are constants.

---

## F. Still unsent

**17. Should `spVar` keep the leftover temporal noise of the bright average?** *(Low priority
— raised 2026-10-06; send after the rig session.)* `spVar` is the local variance of the
**average** of the N bright frames. Averaging does not remove each frame's random noise
(shot noise, read noise, dynamic speckle); it only divides it by N, so `spVar` contains that
leftover as well as the real illumination non-uniformity. Measured on the lab recording
(`tools/rehearsal.py`, 2026-10-05): `spVar` ≈ 1.22 with N = 60 and ≈ 0.51 with N = 600, which
fits *real ≈ 0.44 + 47 / N* DU², where 47 matches the variance of a single frame (raw κ² ×
⟨I⟩² ≈ 0.093 × 21.9² ≈ 45). At N = 600 the leftover is ≈ 0.08 DU² and lowers κ²_corr by about
**1.9 %**; at N = 60 by about 18 %. The MATLAB script computes `spVar` the same way, so Python
and MATLAB agree. Is this intended, or should the leftover — about the single-frame variance
divided by N — be subtracted from `spVar`?

---

## Notes on where these came from

Questions **1, 2, 3 and 8 are new** — they are not in either review or the merged worklist.
They surfaced on 2026-09-07 from a close re-reading of `docs/session_tab`, which specifies
`Params ( struct )` and `startTime` without giving a format, and describes the intensity
check as being over "the picture" while the code computes `mean_i` over the ROI. These are
exactly the mismatches that make a file fail to load as expected on the MATLAB side.

Questions 4, 5, 6, 7 and 12 are worklist Q2, Q4, Q3, Q5 and Q6 respectively.
Questions 13, 14 and 15 come from `Implementation_Plan.md` §9.

## Answered

### End of session, normalization and files — answered 2026-10-07

Reply to the message of 2026-10-04 (questions 7–16). Questions 9 and 14 are still open; see
section B.

**7. Figure format.** **`.png` is fine as a first version.** *Later* she wants a figure that
can be reopened and explored (zoom, pan) in Python, not MATLAB. Either save an interactive
HTML file next to the PNG, or add a button that rebuilds the plot from `rBfi_results.h5`.
→ todo F5. Note: `--mock-h5` already reads a results file, but it *replays* it in real time
through the live plot. It does not open a finished session in one step.

**8. The 90 % laser-off check — confirmed as built.** *Asked:* "average intensity of the
picture" — whole frame or ROI? Compared with the last value, or a mean over the last N
seconds? *Answer:* she confirmed all three points of the built version. No change.
- **Mean over the ROI**, not the whole frame ("it definitely should use the mean over ROI").
  It is the same quantity saved as `Intensity`. A whole-frame mean would be diluted by
  background pixels that never saw laser light, and a real 90 % drop would look smaller.
- **Reference = mean of the last 5 s** of the measurement, not the single last frame ("Great").
  One frame is noisy, and a shadow on the last frame would set the reference too low.
- **Both values dark-subtracted** ("Of course"). The black level (~100 DU) does not go away
  with the laser, so raw values could never fall by 90 %.

In the code: `_LASER_OFF_DROP_FRACTION = 0.90`, `_LASER_OFF_REF_SECONDS = 5.0` and
`_LASER_OFF_TIMEOUT_MS = 2000` in `gui/main_window.py`; tests in
`tests/test_laser_off_check.py`; full story in todo Done item 28.

**10. The 120 s short/long threshold — confirmed.** Measured on the total recording length,
normalization window included (`timeVec(end)`, as in the reference). No change. The rule is
`choose_norm_method()` in `core/session.py` (`NORM_LONG_RECORDING_S = 120.0`); todo Done
item 23.

**11. Stopping before the normalization window ends — CHANGED: normalize on whatever data
exists.** It used to write no `rBFi` in that case. → todo rig-prep row 4c. When it is built,
the window recorded in `Params` must be the span actually used, not the spinbox value. If the
run has no valid BFi at all, there is still nothing to normalize and no `rBFi`. The code to
change is `_finalize_normalization()` and `_write_rbfi()` in `gui/main_window.py`.

**12. The Frames folder — one file per frame.** Use the format that is most efficient to
write, store and read, given that the full raw data must be kept. Format still to be chosen
(benchmark, see todo F1). This is **not** what the code does today: `append_frame` adds every
frame to one growing `frames` dataset inside the results file.

**13. Which frames — every frame.** The purpose is to rerun the algorithm on the saved frames
for research and debugging. Hours-long recordings are not intended to be saved this way, but
the option should exist.

**15. Multi-hour normalization — keep the initial normalization.** A fixed baseline from the
first `norm_seconds`, as now. No rolling re-normalization.

**16. One calibration file — confirmed.** One `Calibration.h5` with a `dark` and a `bright`
group, as built. `session_tab`'s two separate files no longer apply.

### Results-file schema — all six answered 2026-09-23

**1. `startTime` format.** A MATLAB `datetime`, rendered as MATLAB's default display format:
`07-Sep-2026 15:41:47` (`dd-Mmm-yyyy HH:MM:SS`). Stored as that string, so `datetime(str)`
reads it back directly.

**2. `Params` struct — exact field list.** `frameRate`, `exposureTime`, `gain`, `windowSize`,
`ROI` (cx, cy, r), `bitDepth`, the normalization constant and which method produced it, the
normalization window in seconds, and the git commit hash of the code that produced the file.
**`satCapacity` is explicitly excluded** — it must not be used any more and must not appear in
the results. *Implemented 2026-09-23:* renamed to `test_mode_sat_capacity` (synthetic
`--mock-tiff` only) and removed from `folder_camera`, `load_calibration_mat` and the HDF5
metadata.

**3. `Intensity`.** Mean intensity per frame over the **ROI only**, in DU. (Matches what
`processor.process()` already returns as `mean_i`.)

**4. Calibration duplication.** Remove the `calibration` group from the session/results file.
Calibration goes in its own file, holding both kinds — dark and bright. *See question 16: this
conflicts with `session_tab`, which names two separate files.*

**5. When is `rBFi` written?** Buffer the raw BFi **on disk, into the `.h5` file**, as the
session runs, and write the final `rBFi` once at close, so a large dataset is never rewritten.
The trade-off is accepted: a crash mid-session leaves raw BFi and the normalization constant
in the file, but no `rBFi`.

**6. NaN tolerance.** Plain `NaN` is enough — no separate `valid` mask dataset.

### Earlier answers

**G[DU/e] source. ANSWERED 2026-09-22.** Always `LoadGainFromTable`, never `convert_gain`.
CameraSN + nBits missing from the table → error dialog, refuse to measure, with the exact
wording "Can't calculate SCOS: CameraSN <> Mono<> was not found in G[DU/e] Calibration file".
Gain not in the table for a camera that *is* present → warning dialog, continue.
*Implemented 2026-09-22.*

**Plot x-axis units (worklist Q1).** Seconds for ≤ 120 s, minutes for > 120 s. Confirmed
against `SCOSvsTime_WithNoiseSubtraction_Ver2.m:507-513`.

**Normalization window length.** User-configurable in the GUI, not the hardcoded 10 s from the
reference script. Confirmed in `docs/session_tab`.
