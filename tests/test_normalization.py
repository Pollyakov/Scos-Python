"""
Tests for short-vs-long rBFi normalization — worklist task 10 / todo E1.

The reference, `SCOSvsTime_WithNoiseSubtraction_Ver2.m:498-514`:

    if timeVec(end) > 120
        timeToPlot = timeVec / 60; xLabelStr = 'time [min]';
        rBFi = BFi/mean(BFi(1:round(10*frameRate)));
    else
        timeToPlot = timeVec; xLabelStr = 'time [sec]';
        rBFi = BFi/prctile(BFi(1:round(10*frameRate)),5);
    end

Three decisions were taken on 2026-09-28 where the port had a choice:

  * **Total duration decides.** `timeVec(end)` is the whole recording,
    baseline window included, not the time left after it.
  * **The window is the first N seconds**, N from the GUI spinbox. MATLAB
    hardcodes 10 s; `docs/session_tab` says the operator sets it.
  * **"Pulsation lower level" forces the percentile** whatever the length.
    The automatic rule applies only in the default "Number of seconds" mode.

The first was confirmed by the supervisor on 2026-10-07 (question 10 in
docs/open_questions.md). Her answer to question 11 added a fourth rule: a run
stopped before the window closes is normalized on whatever it collected
(TestEarlyStop).

MATLAB's `prctile` is not numpy's default percentile, and the difference lands
directly in the divisor of every point in the results file — so it is tested
against values worked out by hand from MATLAB's own rule.
"""

import sys
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The QApplication must exist before gui.main_window is imported — see CLAUDE.md.
_app = QApplication.instance() or QApplication([])

from core.session import (NORM_LONG_RECORDING_S, NORM_METHOD_MEAN,
                          NORM_METHOD_PERCENTILE, State, choose_norm_method,
                          normalization_constant)
from gui.main_window import MainWindow
from gui.plot_widget import PlotWidget


class TestChooseNormMethod:
    """Which statistic, given how long the recording turned out to be."""

    @pytest.mark.parametrize("duration, expected", [
        (0.0,    NORM_METHOD_PERCENTILE),
        (30.0,   NORM_METHOD_PERCENTILE),
        (119.9,  NORM_METHOD_PERCENTILE),
        (120.0,  NORM_METHOD_PERCENTILE),   # the reference tests `> 120`, not `>=`
        (120.1,  NORM_METHOD_MEAN),
        (3600.0, NORM_METHOD_MEAN),
    ])
    def test_duration_decides(self, duration, expected):
        assert choose_norm_method(duration) == expected

    def test_threshold_is_the_reference_value(self):
        assert NORM_LONG_RECORDING_S == 120.0

    @pytest.mark.parametrize("duration", [10.0, 120.0, 7200.0])
    def test_pulsation_mode_forces_the_percentile(self, duration):
        # The operator asked for the diastolic floor explicitly; a four-hour
        # recording must not silently switch them back to the mean.
        assert choose_norm_method(duration, force_percentile=True) \
            == NORM_METHOD_PERCENTILE


class TestNormalizationConstant:
    """The divisor itself — and MATLAB's percentile convention."""

    def test_mean_is_the_plain_mean(self):
        assert normalization_constant([1.0, 2.0, 3.0, 4.0], NORM_METHOD_MEAN) \
            == pytest.approx(2.5)

    def test_percentile_matches_matlab_not_numpy_default(self):
        # MATLAB places the sorted values at (i-0.5)/n = 12.5, 37.5, 62.5,
        # 87.5 %. The 5th percentile is below the first of those, so prctile
        # clamps to the minimum: 1.0. numpy's default would interpolate to
        # 1.15 — a 15 % error in the divisor of every plotted point.
        values = [1.0, 2.0, 3.0, 4.0]
        assert normalization_constant(values, NORM_METHOD_PERCENTILE) \
            == pytest.approx(1.0)
        assert np.percentile(values, 5) == pytest.approx(1.15)

    def test_percentile_interpolates_the_matlab_way(self):
        # A point that lies between two plotting positions, so clamping cannot
        # hide a wrong interpolation rule: 40 % sits between 37.5 % (value 2)
        # and 62.5 % (value 3), so MATLAB gives 2 + (40-37.5)/25 = 2.1.
        assert float(np.percentile([1.0, 2.0, 3.0, 4.0], 40, method="hazen")) \
            == pytest.approx(2.1)

    def test_nans_are_ignored(self):
        # kappa^2 <= 0 stores NaN, and a NaN anywhere in the window would
        # otherwise make the constant NaN and every rBFi point NaN with it.
        assert normalization_constant([2.0, np.nan, 4.0], NORM_METHOD_MEAN) \
            == pytest.approx(3.0)

    def test_no_finite_values_is_an_error(self):
        with pytest.raises(ValueError):
            normalization_constant([np.nan, np.inf], NORM_METHOD_MEAN)

    def test_unknown_method_is_an_error(self):
        with pytest.raises(ValueError):
            normalization_constant([1.0, 2.0], "median")


class TestPlotAxisUnit:
    """Seconds for a short recording, minutes for a long one."""

    @pytest.fixture
    def plot(self):
        w = PlotWidget()
        yield w
        w.close()

    def _unit(self, plot):
        return plot.graph.getAxis("bottom").labelUnits

    def test_starts_in_seconds(self, plot):
        assert self._unit(plot) == "s"

    def test_stays_in_seconds_below_the_threshold(self, plot):
        plot.append(119.0, 10.0)
        plot._refresh()
        assert self._unit(plot) == "s"

    def test_switches_to_minutes_once_past_it(self, plot):
        # The recording is still growing, so the switch has to happen live
        # rather than being decided up front as it is in MATLAB.
        plot.append(121.0, 10.0)
        plot._refresh()
        assert self._unit(plot) == "min"

    def test_reset_returns_to_seconds(self, plot):
        plot.append(200.0, 10.0)
        plot._refresh()
        plot.reset()
        assert self._unit(plot) == "s"

    def test_rescale_multiplies_every_point(self, plot):
        for v in (10.0, 20.0, 30.0):
            plot.append(1.0, v)
        plot.rescale(2.0)
        _, bfi = plot.get_data()
        np.testing.assert_allclose(bfi, [20.0, 40.0, 60.0])

    @pytest.mark.parametrize("factor", [0.0, -1.0, float("nan"), float("inf")])
    def test_rescale_refuses_a_useless_factor(self, plot, factor):
        plot.append(1.0, 10.0)
        plot.rescale(factor)
        _, bfi = plot.get_data()
        np.testing.assert_allclose(bfi, [10.0])


class _FakeCamera(QObject):
    frame_ready   = pyqtSignal(np.ndarray, float)
    display_ready = pyqtSignal(np.ndarray)
    error         = pyqtSignal(str)
    warning       = pyqtSignal(str)
    pixel_format  = "Mono12"
    exposure_us   = 8000
    gain_db       = 8.0
    frame_rate    = 20.0
    trigger_mode  = "Off"
    trigger_delay = 0

    def set_trigger(self, *a, **k):      pass
    def set_exposure(self, *a, **k):     pass
    def set_gain(self, *a, **k):         pass
    def set_frame_rate(self, *a, **k):   pass
    def set_pixel_format(self, *a, **k): pass
    def start_capture(self, *a, **k):    pass
    def stop(self, *a, **k):             pass
    def close(self, *a, **k):            pass
    def get_info(self):
        return {"serial": "40513592", "model": "a2A1920-160umPRO"}


@pytest.fixture
def dialogs(monkeypatch, tmp_path):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    seen = {"critical": [], "warning": []}

    def _grab(kind):
        def _fn(parent, title, text, *a, **k):
            seen[kind].append((title, text))
            return QMessageBox.StandardButton.Ok
        return _fn

    monkeypatch.setattr(QMessageBox, "critical", _grab("critical"))
    monkeypatch.setattr(QMessageBox, "warning",  _grab("warning"))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    # Stop from a measurement ends with the probe-removal window (U2), shown
    # after the session is saved; recorded here instead of opened.
    seen["probe"] = []
    from gui import safety_dialog
    monkeypatch.setattr(safety_dialog, "show_probe_removal",
                        lambda parent, outcome: seen["probe"].append(outcome))
    return seen


@pytest.fixture
def window(tmp_path):
    """A MainWindow in MEASURING_INIT with a recorder open, as after calibration."""
    w = MainWindow(camera=_FakeCamera())
    w._session_folder = tmp_path
    w._roi_circ = {"cx": 608.0, "cy": 400.0, "r": 150.0}
    w.processor.bit_depth = 12
    w.processor.gain_db   = 8.0
    assert w._prepare_gain()
    w._start_recorder()
    w._norm_seconds = 5.0
    w.btn_start_scos.blockSignals(True)
    w.btn_start_scos.setChecked(True)
    w.btn_start_scos.blockSignals(False)
    w._set_state(State.MEASURING_INIT)
    yield w
    # Closing mid-measurement runs the whole Stop path (files, figure, the
    # probe window — tests/test_laser_off_check.py); not wanted on teardown.
    w._set_state(State.PREVIEW)
    w.close()


# kappa^2 values whose BFi (= 1/kappa^2) spans a range, so the mean and the
# 5th percentile differ by enough that the wrong one is unmistakable.
_K2_CYCLE = [0.10, 0.08, 0.125, 0.09, 0.11]


def _run(window, duration_s, fps=40.0):
    """Feed a whole recording of `duration_s` through the real handler."""
    n = int(duration_s * fps)
    for i in range(n + 1):
        k2 = _K2_CYCLE[i % len(_K2_CYCLE)]
        window._on_scos_result(i / fps, 0.09, k2, 500.0, 1.0)


def _baseline_bfi(window):
    return [b for _, b in window._bfi_norm_buffer]


class TestSessionPicksTheRightConstant:
    """End to end through MainWindow, the way a session actually runs."""

    def test_short_recording_finishes_on_the_percentile(self, window, dialogs):
        _run(window, 20.0)
        assert window._state is State.MEASURING, "normalization must have completed"
        assert window._bfi_norm_method == NORM_METHOD_MEAN, (
            "the live constant is provisional: at 5 s nothing yet says the "
            "recording will be short"
        )

        window._finalize_normalization()

        assert window._bfi_norm_method == NORM_METHOD_PERCENTILE
        assert window._bfi_norm == pytest.approx(
            normalization_constant(_baseline_bfi(window), NORM_METHOD_PERCENTILE))

    def test_long_recording_keeps_the_mean(self, window, dialogs):
        _run(window, 130.0)
        provisional = window._bfi_norm

        window._finalize_normalization()

        assert window._bfi_norm_method == NORM_METHOD_MEAN
        assert window._bfi_norm == pytest.approx(provisional), (
            "nothing changed, so the constant must not drift"
        )

    def test_pulsation_mode_uses_the_percentile_from_the_start(self, window, dialogs):
        window._norm_type = "pulsation"
        _run(window, 130.0)

        assert window._bfi_norm_method == NORM_METHOD_PERCENTILE, (
            "the operator's explicit choice is known immediately — no need to "
            "wait for the length"
        )
        window._finalize_normalization()
        assert window._bfi_norm_method == NORM_METHOD_PERCENTILE, (
            "a long recording must not override the explicit choice"
        )

    def test_the_plot_is_rescaled_to_the_final_constant(self, window, dialogs):
        _run(window, 20.0)
        provisional = window._bfi_norm
        _, before = window.plot_widget.get_data()

        window._finalize_normalization()

        _, after = window.plot_widget.get_data()
        assert len(after) == len(before) > 0
        np.testing.assert_allclose(after, before * (provisional / window._bfi_norm),
                                   rtol=1e-9)

    def test_the_window_is_the_first_n_seconds(self, window, dialogs):
        window._norm_seconds = 3.0
        _run(window, 20.0)
        times = [t for t, _ in window._bfi_norm_buffer]
        assert times[0] == pytest.approx(0.0)
        assert max(times) == pytest.approx(3.0, abs=1.0 / 40.0), (
            "the baseline window must close as soon as norm_seconds is reached"
        )


class TestResultsFileGetsTheFinalValues:
    """Whatever _finalize_normalization decides must be what lands on disk."""

    def test_short_session_writes_the_percentile(self, window, dialogs, tmp_path):
        import h5py

        _run(window, 20.0)
        window._finish_session()
        window._stop_recorder()

        expected = normalization_constant(_baseline_bfi(window),
                                          NORM_METHOD_PERCENTILE)
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            p = f["Params"].attrs
            assert p["normalizationMethod"] == NORM_METHOD_PERCENTILE
            assert p["normalizationConstant"] == pytest.approx(expected)
            assert p["normalizationWindowSec"] == pytest.approx(5.0)
            np.testing.assert_allclose(f["rBFi"][:], f["bfi"][:] / expected,
                                       rtol=1e-9)


class TestEarlyStop:
    """Stopped before the baseline window closed — open question 11.

    The supervisor's answer (2026-10-07): "normalize on whatever data exists".
    Until then such a run wrote raw `bfi` and no `rBFi`, which is exactly what
    a short test run at the rig would have produced.
    """

    def _stop_at(self, window, seconds):
        _run(window, seconds)
        assert window._state is State.MEASURING_INIT, (
            "the test needs a run that never reached the end of its window"
        )
        assert window._bfi_norm is None
        window._finish_session()
        window._stop_recorder()

    def test_rbfi_is_written_from_what_was_collected(self, window, dialogs,
                                                      tmp_path):
        import h5py

        self._stop_at(window, 3.0)               # window is 5 s

        # Under 120 s, so MATLAB's rule picks the 5th percentile — over every
        # BFi value the run produced, because the whole run was the baseline.
        expected = normalization_constant(_baseline_bfi(window),
                                          NORM_METHOD_PERCENTILE)
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert "rBFi" in f, "an early stop must still produce rBFi"
            p = f["Params"].attrs
            assert p["normalizationMethod"] == NORM_METHOD_PERCENTILE
            assert p["normalizationConstant"] == pytest.approx(expected)
            np.testing.assert_allclose(f["rBFi"][:], f["bfi"][:] / expected,
                                       rtol=1e-9)

    def test_params_records_the_window_actually_used(self, window, dialogs,
                                                      tmp_path):
        import h5py

        self._stop_at(window, 3.0)

        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert f["Params"].attrs["normalizationWindowSec"] == pytest.approx(3.0), (
                "the baseline was 3 s of data; writing the spinbox's 5 s would "
                "describe a window that never happened"
            )

    def test_the_collected_points_are_plotted(self, window, dialogs):
        _run(window, 3.0)
        assert len(window.plot_widget.get_data()[1]) == 0, (
            "MEASURING_INIT plots nothing until the window closes"
        )

        window._finalize_normalization()

        buffer = window._bfi_norm_buffer
        _, rbfi = window.plot_widget.get_data()
        assert len(rbfi) == len(buffer) > 0, (
            "without these points the saved figure would be empty"
        )
        np.testing.assert_allclose(
            rbfi, np.array([b for _, b in buffer]) / window._bfi_norm, rtol=1e-9)
        # Not left frozen on the "Normalizing — 3.0 / 5 s" countdown.
        assert "stopped early" in window._calib_label.text()

    def test_the_stop_button_path_writes_it(self, window, dialogs, monkeypatch,
                                            tmp_path):
        """Through _toggle_scos, as the Stop SCOS button runs it.

        The other tests call _finish_session() directly; this one goes through
        disable_intake, the state change to FINISHED and _stop_recorder too.
        The laser-off check has its own tests and is stubbed out here.
        """
        import h5py

        monkeypatch.setattr(window, "_laser_off_check", lambda mask: None)
        _run(window, 3.0)
        assert window._state is State.MEASURING_INIT

        window._toggle_scos(False)

        assert window._state is State.PREVIEW
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert "rBFi" in f
            assert f["Params"].attrs["normalizationWindowSec"] == pytest.approx(3.0)
        assert len(window.plot_widget.get_data()[1]) > 0
        assert len(dialogs["probe"]) == 1, "the probe-removal window followed the save"

    def test_no_valid_bfi_still_writes_no_rbfi(self, window, dialogs, tmp_path):
        import h5py

        # Every corrected kappa^2 <= 0: no BFi at all, so nothing to divide by.
        for i in range(20):
            window._on_scos_result(i / 40.0, 0.09, -0.003, 500.0, 1.0)
        window._finish_session()
        window._stop_recorder()

        assert window._bfi_norm is None
        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert "rBFi" not in f
            assert f["bfi"].shape == (20,)

    def test_a_completed_window_still_records_the_spinbox_length(
            self, window, dialogs, tmp_path):
        import h5py

        # The run went past its window: the early-stop path must not touch it.
        _run(window, 8.0)
        window._finish_session()
        window._stop_recorder()

        with h5py.File(tmp_path / "rBfi_results.h5", "r") as f:
            assert f["Params"].attrs["normalizationWindowSec"] == pytest.approx(5.0)


class TestManualExport:
    """The Save button — legacy .mat/.npz next to the real HDF5 results file."""

    def test_scos_data_is_kappa_squared_not_one_over_rbfi(
            self, window, dialogs, monkeypatch, tmp_path):
        import scipy.io

        _run(window, 20.0)
        window._finalize_normalization()
        const = window._bfi_norm

        out = tmp_path / "manual.mat"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        window._save_data()

        m = scipy.io.loadmat(str(out), squeeze_me=True)
        # The plot holds rBFi. Exporting 1/rBFi under a key documented as κ²
        # would be wrong by exactly the normalization constant.
        np.testing.assert_allclose(m["scosData"], 1.0 / (m["rBFi"] * const),
                                   rtol=1e-9)
        assert m["normalizationConstant"] == pytest.approx(const)
        # And the κ² values must be the ones that were actually measured.
        assert float(np.min(m["scosData"])) == pytest.approx(min(_K2_CYCLE), rel=1e-9)
        assert float(np.max(m["scosData"])) == pytest.approx(max(_K2_CYCLE), rel=1e-9)

    def test_scos_time_is_seconds(self, window, dialogs, monkeypatch, tmp_path):
        import scipy.io

        _run(window, 20.0)
        out = tmp_path / "manual.mat"
        monkeypatch.setattr(QFileDialog, "getSaveFileName",
                            staticmethod(lambda *a, **k: (str(out), "")))
        window._save_data()

        m = scipy.io.loadmat(str(out), squeeze_me=True)
        # 20 s at 40 Hz. In the old minutes-based export this would have been
        # a third of a minute and silently mismatched MATLAB's timeVec.
        assert float(np.max(m["scosTime"])) == pytest.approx(20.0, abs=0.05)


class TestPlotIsRenderedAtClose:
    """Task 12 will save this figure; it must not be a tick behind."""

    def test_pending_points_are_drawn_even_when_nothing_is_rescaled(
            self, window, dialogs):
        _run(window, 130.0)          # long → method unchanged → no rescale
        window.plot_widget._dirty = True     # as if points arrived since the tick

        window._finalize_normalization()

        assert not window.plot_widget._dirty, (
            "the curve must be fully drawn when the session ends"
        )
