"""The session figure: rBFi and <I> against time, plus the parameters used.

Protocol step 6g (todo U5): "Create a figure with two axes: rBfi vs time and
<I> vs time. Add text box with all parameters used." The two plots are
stacked, rBFi on top as in the reference script's rBFi figure
(SCOSvsTime_WithNoiseSubtraction_Ver2.m:517-536), but each keeps its own x and
y axis — the user's choice, 2026-10-10.

Drawn from the results file, not from the live plot. The live plot holds
neither <I> nor the NaN rows (κ² ≤ 0), while the file holds both, and the
`rBFi` in it is the final one by construction — the figure can never disagree
with the data saved next to it. Taking an open h5py group rather than a path
lets the session draw it while the recorder still holds the file open, and
lets any old results file be redrawn later (todo F5).

The parameters box shows only what the files record: a setting that was used
but not saved cannot be shown here, and a figure must not claim more than the
data beside it.

Uses matplotlib's Agg canvas directly and never imports pyplot: pyplot would
pick a Qt backend inside the running app and inside the test suite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import matplotlib
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from core.session import NORM_LONG_RECORDING_S

# Fixed pixel size: the file looks the same whatever the window looked like.
FIGURE_SIZE_IN = (16.0, 9.0)
FIGURE_DPI     = 100            # → 1600 × 900 px

# The reference script's rBFi axis: ylim([0 min(10,max(rBFi))]) (Ver2.m:527).
RBFI_YLIM_CAP = 10.0

MISSING = "—"                   # shown for a value the files do not record


def _attr(attrs: Any, key: str) -> Any:
    """An HDF5 attribute as a plain Python value, or None when absent."""
    if attrs is None or key not in attrs:
        return None
    v = attrs[key]
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, np.generic):
        return v.item()
    return v


def _fmt(value: Any, spec: str = "", unit: str = "") -> str:
    if value is None or value == "":
        return MISSING
    text = format(value, spec) if spec else str(value)
    return f"{text} {unit}".rstrip()


def read_calibration_info(path: "str | Path | None") -> dict[str, dict[str, Any]]:
    """The frame counts and window sizes stored in `Calibration.h5`.

    Returns {"dark": {...}, "bright": {...}} with whatever attributes exist;
    an absent file or group gives an empty dict for it. Only small attributes
    are read — the calibration images themselves are not touched.
    """
    info: dict[str, dict[str, Any]] = {"dark": {}, "bright": {}}
    if path is None or not Path(path).is_file():
        return info
    with h5py.File(path, "r") as f:
        for group in info:
            if group in f:
                info[group] = {k: _attr(f[group].attrs, k)
                               for k in f[group].attrs}
    return info


def parameter_lines(results: h5py.Group,
                    calibration: dict[str, dict[str, Any]] | None = None,
                    ) -> list[tuple[str, str]]:
    """(label, value) rows for the parameters box, all read from the files."""
    p    = results["Params"].attrs if "Params" in results else None
    meta = results["metadata"].attrs if "metadata" in results else None
    cal  = calibration or {"dark": {}, "bright": {}}
    t    = results["timeVec"][:] if "timeVec" in results else np.empty(0)
    bfi  = results["bfi"][:] if "bfi" in results else np.empty(0)

    start = None
    if "startTime" in results:
        start = results["startTime"][()]
        start = start.decode("ascii", "replace") if isinstance(start, bytes) else str(start)

    bits = _attr(p, "bitDepth")
    roi  = _attr(p, "ROI")
    roi_text = (f"x {roi[0]:.0f}, y {roi[1]:.0f}, r {roi[2]:.0f} px"
                if isinstance(roi, list) and len(roi) == 3 else MISSING)
    g, g_src = _attr(meta, "gain_du_per_e"), _attr(meta, "gain_source")
    g_text = _fmt(g, ".4g", "DU/e")
    if g is not None and g_src:
        g_text += f" ({g_src})"
    n_invalid = int(np.count_nonzero(~np.isfinite(bfi))) if bfi.size else 0

    def frames(group: str) -> str:
        n = cal.get(group, {}).get("n_frames")
        return _fmt(n, "d" if isinstance(n, int) else "", "frames")

    return [
        ("Start time",      _fmt(start)),
        ("Duration",        _fmt(float(t[-1]) if t.size else None, ".1f", "s")),
        ("Frames",          f"{t.size}" + (f" ({n_invalid} with κ² ≤ 0)" if n_invalid else "")),
        ("Camera",          _fmt(_attr(meta, "camera_model"))),
        ("Camera SN",       _fmt(_attr(meta, "camera_sn"))),
        ("Pixel format",    f"Mono{bits}" if bits is not None else MISSING),
        ("Exposure",        _fmt(_attr(p, "exposureTime"), "g", "ms")),
        ("Gain",            _fmt(_attr(p, "gain"), "g", "dB")),
        ("G",               g_text),
        ("Frame rate",      _fmt(_attr(p, "frameRate"), "g", "Hz")),
        ("Window size",     _fmt(_attr(p, "windowSize"), "", "px")),
        ("ROI",             roi_text),
        ("Dark cal.",       frames("dark")),
        ("Bright cal.",     frames("bright")),
        ("Norm. type",      _fmt(_attr(meta, "normalization_type"))),
        ("Normalization",   _fmt(_attr(p, "normalizationMethod"))),
        ("Norm. window",    _fmt(_attr(p, "normalizationWindowSec"), ".1f", "s")),
        ("Norm. constant",  _fmt(_attr(p, "normalizationConstant"), ".6g")),
        ("Time source",     _fmt(_attr(meta, "time_source"))),
        ("Lost at camera",  _fmt(_attr(meta, "frames_lost_camera"), "", "frames")),
        ("Dropped (queue)", _fmt(_attr(meta, "frames_dropped_queue"), "", "frames")),
        ("Code version",    _fmt(_attr(p, "gitCommit"))),
    ]


def build_results_figure(results: h5py.Group,
                         calibration: dict[str, dict[str, Any]] | None = None,
                         title: str = "") -> Figure | None:
    """The figure for one session, or None when the file has no rBFi.

    No rBFi means no valid BFi at all (or a session that never finished); a
    figure without its main curve looks like a failed measurement, so none is
    drawn — the same rule the PNG followed before.
    """
    if "rBFi" not in results or "timeVec" not in results:
        return None
    t     = results["timeVec"][:]
    rbfi  = results["rBFi"][:]
    inten = results["Intensity"][:] if "Intensity" in results else None
    if t.size == 0 or rbfi.size == 0:
        return None

    # Minutes over 120 s, seconds otherwise — the reference script's switch
    # (Ver2.m:498-505) and the live plot's.
    if t[-1] > NORM_LONG_RECORDING_S:
        x, x_label = t / 60.0, "time [min]"
    else:
        x, x_label = t, "time [sec]"

    fig = Figure(figsize=FIGURE_SIZE_IN, dpi=FIGURE_DPI)
    FigureCanvasAgg(fig)
    grid = fig.add_gridspec(2, 2, width_ratios=(3.2, 1.0),
                            left=0.06, right=0.98, top=0.92, bottom=0.08,
                            hspace=0.35, wspace=0.05)
    # Two separate axes, nothing shared: each plot zooms on its own.
    ax_bfi = fig.add_subplot(grid[0, 0])
    ax_int = fig.add_subplot(grid[1, 0])
    ax_box = fig.add_subplot(grid[:, 1])

    # NaN rows (κ² ≤ 0) stay NaN, so they show as gaps in the line.
    ax_bfi.plot(x, rbfi, linewidth=0.8, color="#1f77b4")
    ax_bfi.set_xlabel(x_label)
    ax_bfi.set_ylabel("rBFi")
    ax_bfi.set_title("rBFi")
    ax_bfi.grid(True, alpha=0.4)
    finite = rbfi[np.isfinite(rbfi)]
    if finite.size and finite.max() > 0:
        ax_bfi.set_ylim(0.0, min(RBFI_YLIM_CAP, float(finite.max())))

    if inten is not None and inten.size:
        ax_int.plot(x, inten, linewidth=0.8, color="#d62728")
    ax_int.set_xlabel(x_label)
    ax_int.set_ylabel("<I> [DU]")
    ax_int.set_title("Mean intensity in the ROI")
    ax_int.grid(True, alpha=0.4)

    ax_box.axis("off")
    rows  = parameter_lines(results, calibration)
    width = max(len(label) for label, _ in rows)
    text  = "\n".join(f"{label:<{width}}  {value}" for label, value in rows)
    ax_box.text(0.02, 1.0, "Parameters", transform=ax_box.transAxes,
                fontsize=12, fontweight="bold", va="top")
    ax_box.text(0.02, 0.95, text, transform=ax_box.transAxes,
                fontsize=9, family="monospace", va="top", linespacing=1.5,
                bbox={"boxstyle": "round,pad=0.6", "facecolor": "#f4f4f4",
                      "edgecolor": "#999999"})

    if title:
        fig.suptitle(title, fontsize=13)
    return fig


def save_results_figure(results: h5py.Group, out_path: "str | Path",
                        calibration_path: "str | Path | None" = None,
                        title: str = "") -> int:
    """Write the session figure as a PNG. Returns the number of rBFi points.

    Returns 0 and writes nothing when there is no rBFi to draw. Anything else
    that goes wrong raises — the caller decides whether a failed figure may
    affect the session (it may not: MainWindow._save_plot_figure).
    """
    fig = build_results_figure(results, read_calibration_info(calibration_path),
                               title)
    if fig is None:
        return 0
    # Drawing a long line in pieces is much faster. Measured on the rig PC,
    # 2026-10-10, one noisy line: 576 000 points 7.5 s → 1.4 s, 1 152 000
    # points 7.7 s → 1.2 s. This runs on the GUI thread at FINISHED.
    with matplotlib.rc_context({"agg.path.chunksize": 10000}):
        fig.savefig(str(out_path), dpi=FIGURE_DPI)
    return int(results["rBFi"].shape[0])
