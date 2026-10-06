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

Worklist Q7 (the `_DropOldestQueue` sentinel eviction) is deliberately excluded — it is an
internal engineering decision, not a scientific or spec one.

---

## B. End of session — tasks 11 and 12

> **Status 2026-09-28:** task 11 (the laser-off popup and the 90 % check) is implemented,
> with reasonable defaults standing in for the answers to questions 8 and 9. Both are one
> constant away from changing — `_LASER_OFF_DROP_FRACTION` and `_LASER_OFF_REF_SECONDS` in
> `gui/main_window.py`. Tests: `tests/test_laser_off_check.py`.

**7. Figure format.** `session_tab` asks for `rBfi_fig.fig`, but `.fig` is MATLAB-native and
Python cannot write it. Is `.png` acceptable? (`timeVec` and `rBFi` are written to `rBfi_results.h5` in the same folder, so a real
`.fig` can still be rebuilt in MATLAB from the same session.)

**8. The 90 % intensity check.** "Average intensity of the picture" — whole frame, or ROI
only? And compared against which baseline: the single last measurement value, or the mean
over the last N seconds?

> **Implemented default, pending your confirmation:**
> - **ROI, not the whole frame.** It is the region the measurement actually used, and it is
>   the same quantity written as `Intensity` in `rBfi_results.h5` (your answer to question
>   3), so the before/after comparison is like for like. A whole-frame mean would be diluted
>   by background pixels that never saw laser light, making a genuine 90 % drop look smaller
>   than it is and producing false warnings.
> - **The mean over the last 5 seconds, not the single last value.** One frame's ROI mean is
>   noisy, and a momentary shadow across the sensor on the very last frame would set the
>   reference far too low and let a laser that is still on pass the check.
> - **Both sides are dark-subtracted**, exactly as `process()` computes `mean_i`. This one is
>   not really a choice: the camera's black level (100 DU on this rig) does not go away when
>   the laser does, so a comparison of raw DU could never fall by 90 % however completely the
>   laser was switched off — the check would fail on every single run.

**9. If the check fails**, should the app block saving, or warn and let the operator
continue? (Assumed: warn with "Continue anyway?" and continue.)

> **Implemented default:** warn and continue, exactly as assumed. Answering **No** to
> "Continue anyway?" does *not* discard the session — it re-prompts for the laser and runs
> the check again, so the operator can fix the laser and re-verify. The data is already
> recorded by that point; refusing to save it would punish them for a laser switch.

---

## C. Normalization — task 10

> **Status 2026-09-28:** task 10 is implemented, following the MATLAB reference for both of
> these. Question 10 was answered provisionally as *total duration, baseline window included*
> (`timeVec(end)`, exactly as in the reference). Question 11 keeps the existing behaviour: a
> recording that stops before the window closes gets no `rBFi` at all. Both are one-line
> changes if the answer differs — the rules are pure functions in `core/session.py`.

**10. The 120 s short/long threshold** — measured on the total recording duration including
the normalization window, or on the time remaining after it?

**11. Recording shorter than the normalization window** — e.g. the window is 5 s but the
operator stops at 3 s. What should happen: normalize on what exists, refuse to normalize, or
refuse to stop?

---

## D. Later phases — not blocking now

**12. The `Frames` folder.** Individual frame files (one TIFF per frame), or the current
approach of one growing HDF5 dataset, just placed inside that folder? This changes the I/O
design significantly.

**13. Save-frames policy.** Every frame (~280 GB over 4 hours), every K-th frame, or
calibration frames only?

**14. Disk-space policy.** How much free space should be required to allow a recording to
start, and at what remaining threshold should a running session stop gracefully?

**15. Multi-hour normalization.** Over several hours, laser drift, detector heating and
subject motion may invalidate a fixed baseline taken from the first N seconds. Keep the
fixed baseline, use rolling re-normalization, or save raw BFi and normalize offline?

---

## E. New — raised by the answers of 2026-09-23

**16. One calibration file, or two?** The answer to question 4 says to remove the embedded
calibration group and write *"one separate file with 2 kinds of calibrations — dark and
bright"*. But `docs/session_tab` lists the session folder as containing **two** files,
`DarkCalibration.h5` **and** `BrightCalibration.h5`, and worklist task 9 was written against
that. Which is it: one combined file (proposed name `Calibration.h5`, with a `dark` group and
a `bright` group), or the two separate files named in `session_tab`?

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
