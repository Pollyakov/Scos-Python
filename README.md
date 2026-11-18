# SCOS — real-time cerebral blood flow measurement

SCOS (Speckle Contrast Optical Spectroscopy) measures blood flow in the brain with a laser
and a camera. This app shows the camera picture, runs the dark and bright calibrations,
computes the speckle contrast κ² with noise correction for every frame, and plots the
relative blood flow index (rBFi) live. Every session is saved automatically.

Written for the Optical Neuroimaging Lab at Bar-Ilan University. The calculation follows
[`docs/SCOS_protocol.md`](docs/SCOS_protocol.md) and was checked against the lab's MATLAB
script on 600 real frames (agreement to 3 × 10⁻⁷).

**Windows 10 / 11 only.** It works with Basler cameras whose gain was measured by the lab —
see [Which cameras work](#which-cameras-work).

> **Laser safety.** The subject must wear laser-safety goggles for the whole measurement.
> Remove the probe only after checking that the laser is off. The app reminds you at start
> and at the end of every session — it does not replace the lab's safety training.

---

## Install

You need about 15 minutes, an internet connection and 2 GB of free disk space.

### Step 1 — install three programs (once per PC)

| Program | Where | What to watch for |
|---|---|---|
| **Python 3.13** | <https://www.python.org/downloads/windows/> | On the installer's first page tick **"Add python.exe to PATH"** |
| **Git** | <https://git-scm.com/download/win> | The default answers are fine |
| **Basler pylon Software Suite** | <https://www.baslerweb.com/en/downloads/software/> | Needed only on a PC with a camera — it holds the camera's driver |

For **External Trigger** (the Arduino that fires the laser and the camera together) the PC
also needs **Arduino IDE 2** (<https://www.arduino.cc/en/software>). Without it everything
else still works.

### Step 2 — download SCOS

Open **Command Prompt** (Start menu → type `cmd`), go to the folder where SCOS should live,
and run:

```
git clone https://github.com/Pollyakov/Scos-Python.git
```

This makes a folder `Scos-Python`. Do not move it later — the desktop icon points at it
(if you must move it, run `setup.bat` again).

*Why Git and not "Download ZIP"?* Every results file records which version of the code
produced it. From a ZIP download the app cannot know its version and writes `unknown`.

### Step 3 — run `setup.bat`

Open the `Scos-Python` folder and **double-click `setup.bat`**. It:

1. checks that Python is new enough,
2. makes the project's own Python environment (the folder `venv`),
3. installs the libraries, in exactly the versions the app was tested with,
4. puts the **SCOS icon** on the desktop,
5. checks the installation and prints the result.

It takes a few minutes. At the end you should see **"Installation complete."** Lines marked
`NOTE` are information (for example "no Basler camera found" on a PC without a camera);
a line marked `PROBLEM` must be fixed first — its text says how.

If something fails, fix what the message says and run `setup.bat` again: it is safe to run
any number of times.

---

## Start the app

**Double-click the SCOS icon on the desktop.** A "Starting SCOS…" sign appears for about
ten seconds, then the laser-safety warning, then the main window.

Only one copy can run at a time. If the app stops with an error, the file `app.log` in the
`Scos-Python` folder says what happened — send it along when you report a problem.

### Try it without a camera

Make a synthetic recording once, then play it (in Command Prompt, inside `Scos-Python`):

```
venv\Scripts\python.exe tools\synth_tiff.py --out scratch\mock.tif --frames 300
venv\Scripts\python.exe main.py --mock-tiff scratch\mock.tif
```

The numbers have no biological meaning — this only shows that the app runs and lets you
learn the buttons. To replay a real recording folder (one TIFF per frame, with its `_dark`
folder beside it):

```
venv\Scripts\python.exe main.py --mock-folder "D:\path\to\recording_folder"
```

---

## A measurement in short

1. **Start Video** — the live picture appears.
2. Draw the **ROI** circle on the lit area; optionally **Cut Image** so the camera sends
   only that region (faster).
3. Type a **Recording name** and press **Start SCOS**. The app then asks, in this order:
   the output folder → *turn off the laser* (dark calibration) → *turn on the laser*
   (bright calibration, with the subject in place).
4. The measurement runs and rBFi is plotted. Watch the **Dropped** counter in the Info box:
   grey `Dropped: 0` is good, a red block means frames were lost.
5. **Stop SCOS** (or the set duration ends) → *turn off the laser* → the files are saved.

Each session gets its own folder `<Recording name>_<date>_<time>` with:

| File | What is in it |
|---|---|
| `rBFi_resultsAndParameters.h5` | time, rBFi, mean intensity, κ² before and after correction, all parameters |
| `Calibration.h5` | the dark and bright calibrations |
| `rBfi_fig.png` | rBFi and intensity against time, with the parameters |
| `app.log` | everything the app did during the session |
| `Frames\` | every frame as a TIFF — only when **Save Frames** is ticked (≈ 5.7 GB per minute at full frame) |

To check a finished session: `venv\Scripts\python.exe tools\check_session.py "<session folder>"`.

---

## Which cameras work

The app takes the camera gain G [DU/e] only from the lab's measured table,
[`CamerasMeasuredGain.csv`](CamerasMeasuredGain.csv), by the camera's **serial number** and
bit depth. A camera that is not in the table shows its picture but **cannot be measured
with** — the app says so and stops. To use a new camera, its gain must be measured first and
a row added to the table.

`setup.bat` (and `tools\check_install.py`) tell you whether the connected camera is in the
table.

---

## Update to a newer version

In Command Prompt, inside `Scos-Python`:

```
git pull
setup.bat
```

Your last-used settings (`scos_config.local.json`) and your recordings are not touched.

## Versions

| Tag | Date | What it is |
|---|---|---|
| `v0` | 2026-11-17 | The complete measurement: calibrations, noise-corrected κ², rBFi, results files, Cut Image, Save Frames. Checked against MATLAB |

To use one exact version: `git checkout v0` (then `setup.bat`).

---

## For developers

```
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m pytest tests/                 (everything, ≈ 7 minutes)
venv\Scripts\python.exe -m pytest tests/ -m "not slow"   (≈ 1 minute)
```

The slow tests compare the app with MATLAB on the lab's recording and need that recording
on the PC. How the code is organised, and the rules that keep the measurement accurate, are
in [`CLAUDE.md`](CLAUDE.md); the task list is [`docs/todo.md`](docs/todo.md).
