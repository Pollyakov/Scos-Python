"""
Tests for core/frame_writer.py — raw frames saved one TIFF per frame on a
background thread, in the layout Vika's MATLAB script reads
(docs/Plan_Frame_Saving.md).
"""

import csv
import os
import re
import threading
import time

import numpy as np
import pytest
import tifffile

import core.frame_writer as fw
from core.frame_writer import FRAME_TIMES_FILENAME, FrameWriter, frames_folder_name

# The pattern folder_camera.py reads the model and serial number with — the
# saved frames must be replayable with --mock-folder.
_PLAYBACK_NAME = re.compile(r"Basler_([^_]+(?:-\w+)*)__(\d+)__")


def _frame(value: int, shape=(6, 8)) -> np.ndarray:
    f = np.arange(shape[0] * shape[1], dtype=np.uint16).reshape(shape)
    return f + np.uint16(value)


@pytest.fixture
def writer(tmp_path):
    w = FrameWriter(tmp_path / "Frames", "expT8ms_Gain8dB_FR20Hz_BL100DU",
                    camera_model="a2A1920-160umBAS", camera_sn="40075248")
    yield w
    w.close()


def _read_csv(frames_dir):
    with open(frames_dir / FRAME_TIMES_FILENAME, newline="") as f:
        return list(csv.reader(f))


class TestFolderName:
    def test_lab_settings(self):
        assert frames_folder_name(8, 8, 20, 100) == "expT8ms_Gain8dB_FR20Hz_BL100DU"

    def test_decimals_kept(self):
        assert frames_folder_name(2.5, 7.25, 40, 100) == "expT2.5ms_Gain7.25dB_FR40Hz_BL100DU"

    def test_unknown_black_level_is_zero(self):
        # Playback and the mock report no BlackLevel.
        assert frames_folder_name(5, 24, 40, None).endswith("_BL0DU")

    def test_matlab_tokens(self):
        # GetParamsFromFileName splits on "_" and takes name + number + units.
        words = frames_folder_name(8, 8, 20, 100).split("_")
        assert [re.match(r"([A-Za-z]+)(\d+(?:\.\d+)?)(\w*)", w).group(1) for w in words] == \
            ["expT", "Gain", "FR", "BL"]


class TestWriting:
    def test_each_phase_in_its_own_folder(self, writer, tmp_path):
        for phase in ("dark", "bright", "measurement"):
            assert writer.put(phase, _frame(1), t_capture=time.monotonic())
        assert writer.close()
        frames = tmp_path / "Frames"
        for sub in ("expT8ms_Gain8dB_FR20Hz_BL100DU_dark",
                    "expT8ms_Gain8dB_FR20Hz_BL100DU_bright",
                    "expT8ms_Gain8dB_FR20Hz_BL100DU"):
            files = os.listdir(frames / sub)
            assert len(files) == 1 and files[0].endswith(".tiff")   # not .tif

    def test_pixels_unchanged(self, writer):
        frame = np.random.default_rng(0).integers(0, 4096, (12, 16), dtype=np.uint16)
        writer.put("measurement", frame, t_capture=time.monotonic())
        writer.close()
        (path,) = writer.phase_dir("measurement").iterdir()
        back = tifffile.imread(path)
        assert back.dtype == np.uint16
        np.testing.assert_array_equal(back, frame)

    def test_names_readable_by_playback(self, writer):
        writer.put("measurement", _frame(0), t_capture=time.monotonic())
        writer.close()
        (path,) = writer.phase_dir("measurement").iterdir()
        m = _PLAYBACK_NAME.match(path.name)
        assert m and m.group(1) == "a2A1920-160umBAS" and m.group(2) == "40075248"

    def test_playback_model_name_not_doubled(self, tmp_path):
        # folder_camera.py reports the model with the "Basler_" already in it.
        w = FrameWriter(tmp_path, "x", camera_model="Basler_a2A1920-160umPRO",
                        camera_sn="40513592")
        w.put("dark", _frame(0), t_capture=time.monotonic())
        w.close()
        (path,) = w.phase_dir("dark").iterdir()
        assert path.name.startswith("Basler_a2A1920-160umPRO__40513592__")

    def test_alphabetical_order_is_capture_order(self, writer):
        # Fixed-width index: frame 10 must not sort between 1 and 2.
        for i in range(12):
            writer.put("dark", _frame(i), t_capture=time.monotonic())
        writer.close()
        names = sorted(os.listdir(writer.phase_dir("dark")))
        values = [int(tifffile.imread(writer.phase_dir("dark") / n)[0, 0]) for n in names]
        assert values == list(range(12))

    def test_file_time_is_capture_time(self, writer):
        # The MATLAB script sorts frames by file modification time.
        t0 = time.monotonic()
        times = [t0 + 0.05 * i for i in range(5)]
        for i, t in enumerate(reversed(times)):    # handed over out of time order
            writer.put("measurement", _frame(i), t_capture=t)
        writer.close()
        paths = sorted(writer.phase_dir("measurement").iterdir())
        mtimes = [p.stat().st_mtime for p in paths]
        np.testing.assert_allclose(np.diff(mtimes), -0.05, atol=1e-3)
        assert abs(max(mtimes) - (time.time() - time.monotonic() + times[-1])) < 1.0

    def test_frame_times_csv(self, writer, tmp_path):
        t = time.monotonic()
        writer.put("dark", _frame(0), t_capture=t)
        writer.put("measurement", _frame(1), t_capture=t + 1.0, time_vec_s=0.0)
        writer.put("measurement", _frame(2), t_capture=t + 1.05, time_vec_s=0.05)
        writer.close()
        rows = _read_csv(tmp_path / "Frames")
        assert rows[0] == ["phase", "file", "index", "t_capture_s", "timeVec_s"]
        assert [r[0] for r in rows[1:]] == ["dark", "measurement", "measurement"]
        assert rows[1][4] == ""                       # no timeVec for calibration frames
        assert [float(r[4]) for r in rows[2:]] == [0.0, 0.05]
        for r in rows[1:]:                            # every row names a real file
            assert (tmp_path / "Frames" / r[1]).is_file()
        assert float(rows[3][3]) - float(rows[2][3]) == pytest.approx(0.05, abs=1e-6)

    def test_counts(self, writer):
        for _ in range(3):
            writer.put("bright", _frame(0), t_capture=time.monotonic())
        writer.close()
        assert writer.totals()["saved"] == {"dark": 0, "bright": 3, "measurement": 0}


class TestNeverBlocks:
    def _stall(self, monkeypatch):
        """Make every write wait until `release` is set; `entered` is set once
        the writer thread has taken a frame off the queue and started writing
        it — waited for instead of a sleep, so a busy PC cannot change the
        counts below."""
        release, self.entered = threading.Event(), threading.Event()
        real = tifffile.imwrite

        def slow(*a, **k):
            self.entered.set()
            release.wait(10)
            return real(*a, **k)
        monkeypatch.setattr(fw.tifffile, "imwrite", slow)
        return release

    def test_full_queue_skips_and_counts(self, tmp_path, monkeypatch):
        release = self._stall(monkeypatch)
        w = FrameWriter(tmp_path, "x", "m", "1", max_queued=2)
        results = [w.put("measurement", _frame(0), t_capture=time.monotonic())]
        assert self.entered.wait(5)                   # the thread holds frame 0
        t = time.perf_counter()
        for i in range(1, 6):
            results.append(w.put("measurement", _frame(i), t_capture=time.monotonic()))
        took = time.perf_counter() - t
        assert took < 1.0                             # put() never waited for the disk
        assert results.count(False) == 3              # 1 being written + 2 queued
        assert w.skipped["measurement"] == 3
        release.set()
        deadline = time.monotonic() + 5
        while w.queued and time.monotonic() < deadline:
            time.sleep(0.01)
        assert w.put("measurement", _frame(9), t_capture=time.monotonic())   # after the skips
        assert w.close()
        assert w.saved["measurement"] == 4
        # The saved ones keep consecutive numbers — a skipped frame leaves no hole.
        names = sorted(os.listdir(w.phase_dir("measurement")))
        assert [n[-11:] for n in names] == ["000000.tiff", "000001.tiff",
                                            "000002.tiff", "000003.tiff"]

    def test_close_with_a_full_queue_does_not_hang(self, tmp_path, monkeypatch):
        release = self._stall(monkeypatch)
        w = FrameWriter(tmp_path, "x", "m", "1", max_queued=2)
        w.put("dark", _frame(0), t_capture=time.monotonic())
        assert self.entered.wait(5)
        for i in (1, 2):                               # fills the 2-frame queue
            assert w.put("dark", _frame(i), t_capture=time.monotonic())
        threading.Timer(0.3, release.set).start()
        assert w.close(timeout=10)
        assert w.saved["dark"] == 3


class TestClose:
    def test_close_writes_what_is_queued(self, writer):
        for i in range(10):
            writer.put("measurement", _frame(i), t_capture=time.monotonic())
        assert writer.close()
        assert writer.saved["measurement"] == 10
        assert len(os.listdir(writer.phase_dir("measurement"))) == 10

    def test_put_after_close_is_counted_not_saved(self, writer):
        writer.close()
        assert writer.put("dark", _frame(0), t_capture=time.monotonic()) is False
        assert writer.skipped["dark"] == 1

    def test_close_twice(self, writer):
        assert writer.close()
        assert writer.close()

    def test_unknown_phase(self, writer):
        with pytest.raises(ValueError):
            writer.put("preview", _frame(0), t_capture=time.monotonic())


class TestWriteFailure:
    def test_failure_is_counted_and_writing_goes_on(self, tmp_path, monkeypatch, caplog):
        real = tifffile.imwrite
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("disk full")
            return real(*a, **k)
        monkeypatch.setattr(fw.tifffile, "imwrite", flaky)
        w = FrameWriter(tmp_path, "x", "m", "1")
        for i in range(3):
            w.put("measurement", _frame(i), t_capture=time.monotonic())
        assert w.close()
        assert w.failed["measurement"] == 1 and w.saved["measurement"] == 2
        assert "could not save" in caplog.text
        rows = _read_csv(tmp_path)
        assert len(rows) == 1 + 2                     # no row for the failed frame


class TestPathLength:
    """Windows' 260-character limit (found 2026-10-21: every bright frame of a
    session in a deep folder failed with FileNotFoundError)."""

    def test_a_path_too_long_is_refused_before_anything_is_written(self, tmp_path):
        deep = tmp_path / ("d" * (fw.MAX_PATH_CHARS - len(str(tmp_path)) - 40))
        with pytest.raises(OSError, match="characters long"):
            FrameWriter(deep / "Frames", "expT8ms_Gain8dB_FR20Hz_BL100DU",
                        "a2A1920-160umBAS", "40075248")
        assert not deep.exists()

    def test_the_longest_file_fits(self, writer):
        writer.put("bright", _frame(0), t_capture=time.monotonic())
        writer.close()
        (path,) = writer.phase_dir("bright").iterdir()
        assert len(str(path)) <= fw.MAX_PATH_CHARS

    def test_name_has_no_timestamp(self, writer):
        writer.put("dark", _frame(0), t_capture=time.monotonic())
        writer.close()
        (path,) = writer.phase_dir("dark").iterdir()
        assert path.name == "Basler_a2A1920-160umBAS__40075248__000000.tiff"
