"""Capture lifecycle tests use synthetic PCM only; never open a microphone."""
import os
from pathlib import Path
from types import SimpleNamespace
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QObject, QMargins, QRect, QSize, Signal, Qt
from PySide6.QtMultimedia import QAudioFormat, QMediaPlayer as NativePlayer, QtAudio
from PySide6.QtWidgets import QApplication, QDialog

from desktop_app import paths, recording


def audio_format(rate=24000, channels=1, sample_format=QAudioFormat.SampleFormat.Int16):
    result = QAudioFormat()
    result.setSampleRate(rate)
    result.setChannelCount(channels)
    result.setSampleFormat(sample_format)
    return result


def pcm(seconds=3.25, rate=24000):
    t = np.arange(round(seconds * rate)) / rate
    return np.rint(np.sin(t * 2 * np.pi * 220) * 8000).astype("<i2").tobytes()


class Device:
    def __init__(self, identity=b"test-microphone", preferred=None, supports_mono=True):
        self.identity = identity
        self.preferred = preferred or audio_format()
        self.supports_mono = supports_mono

    def isNull(self):
        return not self.identity

    def id(self):
        return self.identity

    def description(self):
        return "模拟输入设备"

    def preferredFormat(self):
        return self.preferred

    def isFormatSupported(self, fmt):
        return (fmt == self.preferred or (self.supports_mono and fmt.channelCount() == 1
                                         and fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16))


class Stream(QObject):
    readyRead = Signal()

    def __init__(self):
        super().__init__()
        self.pending = b""

    def readAll(self):
        value, self.pending = self.pending, b""
        return value

    def push(self, data):
        self.pending += data
        self.readyRead.emit()


@pytest.fixture
def capture_env(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, "_SESSION", tmp_path)

    class Devices(QObject):
        audioInputsChanged = Signal()
        audioOutputsChanged = Signal()
        inputs = [Device()]
        output = Device(b"initial-speaker")

        @classmethod
        def audioInputs(cls):
            return cls.inputs

        @classmethod
        def defaultAudioInput(cls):
            return cls.inputs[0] if cls.inputs else Device(b"")

        @classmethod
        def defaultAudioOutput(cls):
            return cls.output

    class Source(QObject):
        stateChanged = Signal(object)
        instances = []
        opening_error = QtAudio.Error.NoError

        def __init__(self, device, fmt, parent=None):
            super().__init__(parent)
            self.instances.append(self)
            self.device, self.format = device, fmt
            self.stream = Stream()
            self.stops = 0
            self.deleted = False
            self.current_error = self.opening_error

        def setBufferSize(self, size):
            self.buffer_size = size

        def start(self):
            self.stateChanged.emit(QtAudio.State.ActiveState)
            return self.stream if self.current_error == QtAudio.Error.NoError else None

        def error(self):
            return self.current_error

        def stop(self):
            self.stops += 1
            self.stateChanged.emit(QtAudio.State.StoppedState)

        def deleteLater(self):
            self.deleted = True

    monkeypatch.setattr(recording, "QMediaDevices", Devices)
    monkeypatch.setattr(recording, "QAudioSource", Source)
    monkeypatch.setattr(recording.RecordingDialog, "_permission_status", lambda self: Qt.PermissionStatus.Granted)
    return SimpleNamespace(app=app, devices=Devices, source=Source, folder=tmp_path)


@pytest.mark.parametrize("sample_format,dtype,values", [
    (QAudioFormat.SampleFormat.UInt8, "u1", [128, 192, 64, 128]),
    (QAudioFormat.SampleFormat.Int16, "<i2", [0, 16384, -16384, 0]),
    (QAudioFormat.SampleFormat.Int32, "<i4", [0, 1073741824, -1073741824, 0]),
    (QAudioFormat.SampleFormat.Float, "<f4", [0, .5, -.5, 0]),
])
def test_partial_input_frames_are_preserved_and_stereo_becomes_mono(sample_format, dtype, values):
    collector = recording.PCMCollector(audio_format(16000, 2, sample_format))
    raw = np.tile(np.array(values, dtype=dtype), 24000).tobytes()
    # A device read may split even a multibyte sample or stereo frame.
    for chunk in (raw[:1], raw[1:7], raw[7:]):
        collector.feed(chunk)
    result = np.frombuffer(collector.validated_pcm(), dtype="<i2")
    assert collector.frames == 48000
    assert collector.duration == 3
    np.testing.assert_array_equal(result[:4], [8192, -8192, 8192, -8192])


def test_sample_count_caps_at_30_seconds_without_wall_clock_rounding():
    collector = recording.PCMCollector(audio_format())
    assert collector.feed(pcm(29.999)) is False
    assert collector.feed(pcm(2)) is True
    assert collector.feed(pcm(1)) is True
    assert collector.frames == 24000 * 30
    assert len(collector.validated_pcm()) == 24000 * 30 * 2


def test_native_float_stereo_is_used_when_mono_pcm_not_supported():
    preferred = audio_format(48000, 2, QAudioFormat.SampleFormat.Float)
    assert recording.capture_format(Device(preferred=preferred, supports_mono=False)) == preferred
    with pytest.raises(ValueError, match="16000"):
        recording.capture_format(Device(preferred=audio_format(8000), supports_mono=False))


def test_recording_requires_start_and_saves_exact_pcm16_frames(capture_env):
    recorder = recording.AudioRecorder()
    results, errors = [], []
    recorder.finished.connect(lambda path, duration: results.append((path, duration)))
    recorder.failed.connect(errors.append)
    assert not capture_env.source.instances
    assert not list(capture_env.folder.iterdir())
    recorder.start()
    source = capture_env.source.instances[0]
    source.stream.push(pcm(3))
    # This read is pending at the instant the user presses stop.
    source.stream.pending = pcm(.25)
    recorder.stop()
    assert not recorder.active and not recorder._timer.isActive()
    assert source.stops == 1 and source.deleted
    assert not errors and len(results) == 1
    path, duration = results[0]
    assert duration == 3.25
    with wave.open(path, "rb") as data:
        assert (data.getnchannels(), data.getsampwidth(), data.getframerate()) == (1, 2, 24000)
        assert data.getnframes() == 78000
        assert data.readframes(78000) == pcm(3) + pcm(.25)
    recorder.cleanup()
    assert not Path(path).exists()


def test_limit_stops_capture_and_emits_one_complete_file(capture_env):
    recorder = recording.AudioRecorder()
    results = []
    recorder.finished.connect(lambda path, duration: results.append((path, duration)))
    recorder.start()
    source = capture_env.source.instances[0]
    source.stream.push(pcm(31))
    recorder.stop()
    assert len(results) == 1 and results[0][1] == 30
    assert not recorder.active and source.stops == 1
    with wave.open(results[0][0]) as data:
        assert data.getnframes() == 720000
    recorder.cleanup()


@pytest.mark.parametrize("raw,reason", [(pcm(2.99), "不足 3 秒"), (b"\0" * 144000, "静音")], ids=["too_short", "silent"])
def test_invalid_capture_does_not_create_saved_audio(capture_env, raw, reason):
    recorder = recording.AudioRecorder()
    errors, results = [], []
    recorder.failed.connect(errors.append)
    recorder.finished.connect(lambda *args: results.append(args))
    recorder.start()
    capture_env.source.instances[0].stream.push(raw)
    recorder.stop()
    assert reason in errors[0]
    assert not results and not list(capture_env.folder.iterdir())
    assert not recorder.active


def test_nonfinite_capture_and_antiphase_silence_are_rejected():
    fmt = audio_format(16000, 2, QAudioFormat.SampleFormat.Float)
    collector = recording.PCMCollector(fmt)
    with pytest.raises(ValueError, match="无效"):
        collector.feed(np.array([0.1, float("nan")], dtype="<f4").tobytes())
    collector = recording.PCMCollector(fmt)
    collector.feed(np.tile(np.array([.5, -.5], dtype="<f4"), 48000).tobytes())
    with pytest.raises(ValueError, match="静音"):
        collector.validated_pcm()
    constant = recording.PCMCollector(audio_format())
    constant.feed(np.full(72000, 1200, dtype="<i2").tobytes())
    with pytest.raises(ValueError, match="静音"):
        constant.validated_pcm()


@pytest.mark.parametrize("cause", ["disconnect", "io_error", "unexpected_stop", "stall"])
def test_capture_failure_discards_audio_and_releases_input(capture_env, monkeypatch, cause):
    recorder = recording.AudioRecorder()
    errors, results = [], []
    recorder.failed.connect(errors.append)
    recorder.finished.connect(lambda *args: results.append(args))
    recorder.start()
    source = capture_env.source.instances[0]
    source.stream.push(pcm(3))
    if cause == "disconnect":
        capture_env.devices.inputs = []
        recorder._devices.audioInputsChanged.emit()
    elif cause == "io_error":
        source.current_error = QtAudio.Error.IOError
        source.stateChanged.emit(QtAudio.State.StoppedState)
    elif cause == "unexpected_stop":
        source.stateChanged.emit(QtAudio.State.StoppedState)
    else:
        recorder._last_data_at -= 6
        recorder._timer.timeout.emit()
    assert len(errors) == 1 and not results
    assert not recorder.active and source.stops == 1
    assert not list(capture_env.folder.iterdir())


def test_open_failure_no_device_and_disconnected_selection_never_save(capture_env):
    recorder = recording.AudioRecorder()
    errors = []
    recorder.failed.connect(errors.append)
    capture_env.source.opening_error = QtAudio.Error.OpenError
    recorder.start()
    assert "权限" in errors[-1]
    assert capture_env.source.instances[0].stops == 1
    capture_env.devices.inputs = []
    recorder.start()
    assert "没有可用" in errors[-1]
    recorder.start(Device(b"unplugged"))
    assert "已断开" in errors[-1]
    assert len(capture_env.source.instances) == 1
    assert not list(capture_env.folder.iterdir())


def test_disk_write_error_discards_partial_and_can_retry(capture_env, monkeypatch):
    recorder = recording.AudioRecorder()
    errors = []
    recorder.failed.connect(errors.append)
    original = recording.wave.open

    def fail_open(path, *args):
        Path(path).write_bytes(b"incomplete")
        raise OSError("磁盘写入失败")

    monkeypatch.setattr(recording.wave, "open", fail_open)
    recorder.start()
    capture_env.source.instances[-1].stream.push(pcm())
    recorder.stop()
    assert errors == ["磁盘写入失败"]
    assert not list(capture_env.folder.iterdir())
    monkeypatch.setattr(recording.wave, "open", original)
    recorder.start()
    capture_env.source.instances[-1].stream.push(pcm())
    recorder.stop()
    assert recorder._audio_path.is_file()
    recorder.cleanup()


def test_dialog_explicit_start_edit_lock_validation_and_result_ownership(capture_env):
    stopped = []
    dialog = recording.RecordingDialog(mode="library", stop_playback=lambda: stopped.append(True))
    assert not capture_env.source.instances and not dialog.save_button.isEnabled()
    assert dialog.transcript_edit.toPlainText() == recording.DEFAULT_TRANSCRIPT
    with pytest.raises(ValueError, match="尚未"):
        dialog.take_result()
    dialog.start_button.click()
    assert stopped == [True]
    assert dialog.transcript_edit.isReadOnly() and not dialog.device_combo.isEnabled()
    source = capture_env.source.instances[-1]
    source.stream.push(pcm())
    dialog.stop_button.click()
    assert not dialog.transcript_edit.isReadOnly() and dialog.device_combo.isEnabled()
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "名称" in dialog.status_label.text()
    dialog.name_edit.setText("我的讲解声音")
    dialog.transcript_edit.setPlainText("实际读出的文字。")
    dialog.accept()
    result = dialog.take_result()
    assert result["name"] == "我的讲解声音" and result["transcript"] == "实际读出的文字。"
    assert Path(result["audio"]).is_absolute() and Path(result["audio"]).is_file()
    result["name"] = "修改副本"
    assert dialog.result_voice["name"] == "我的讲解声音"
    unrelated = capture_env.folder / "user-audio.wav"
    unrelated.write_bytes(b"keep")
    dialog.cleanup()
    assert not Path(result["audio"]).exists() and unrelated.read_bytes() == b"keep"


def test_dialog_rerecord_and_cancel_clean_owned_recordings(capture_env):
    dialog = recording.RecordingDialog(mode="temporary")
    dialog.start_button.click()
    capture_env.source.instances[-1].stream.push(pcm())
    dialog.stop_button.click()
    first = Path(dialog._recorded_audio)
    assert first.is_file()
    dialog.start_button.click()
    assert not first.exists()
    source = capture_env.source.instances[-1]
    source.stream.push(pcm())
    dialog.reject()
    assert not dialog.recorder.active and source.stops == 1
    assert dialog.result_voice is None and not list(capture_env.folder.iterdir())


def test_closing_dialog_stops_capture_without_saving(capture_env):
    dialog = recording.RecordingDialog()
    dialog.show()
    dialog.start_button.click()
    source = capture_env.source.instances[-1]
    source.stream.push(pcm())
    dialog.close()
    assert source.stops == 1 and not dialog.recorder.active
    assert not list(capture_env.folder.iterdir())


def test_permission_denial_and_late_callback_do_not_open_microphone(capture_env, monkeypatch):
    dialog = recording.RecordingDialog()
    monkeypatch.setattr(dialog, "_permission_status", lambda: Qt.PermissionStatus.Denied)
    dialog.start_button.click()
    assert not capture_env.source.instances and "权限" in dialog.status_label.text()
    # A grant from an earlier request must not start recording after Cancel.
    dialog._permission_pending = True
    dialog.reject()
    dialog._permission_ready(SimpleNamespace(status=lambda: Qt.PermissionStatus.Granted))
    assert not capture_env.source.instances


def test_permission_request_is_explicit_and_context_owned(capture_env, monkeypatch):
    pending = []
    monkeypatch.setattr(QApplication, "requestPermission", lambda self, permission, context, callback: pending.append((context, callback)))
    dialog = recording.RecordingDialog()
    monkeypatch.setattr(dialog, "_permission_status", lambda: Qt.PermissionStatus.Undetermined)
    assert not pending and not capture_env.source.instances
    dialog.start_button.click()
    assert len(pending) == 1 and pending[0][0] is dialog
    assert not capture_env.source.instances and not dialog.device_combo.isEnabled()
    pending[0][1](SimpleNamespace(status=lambda: Qt.PermissionStatus.Granted))
    assert len(capture_env.source.instances) == 1 and dialog.recorder.active
    dialog.reject()


def test_recording_preview_rebinds_output_and_stops_when_device_disappears(capture_env, monkeypatch):
    class Output(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.selected = None

        def setDevice(self, device):
            self.selected = device

        def device(self):
            return self.selected

    class Player(QObject):
        PlaybackState = NativePlayer.PlaybackState
        playbackStateChanged = Signal(object)
        errorOccurred = Signal(object, str)

        def __init__(self, parent):
            super().__init__(parent)
            self.state = self.PlaybackState.StoppedState
            self.play_calls = 0

        def setAudioOutput(self, output):
            self.output = output

        def setSource(self, url):
            self.source = url

        def play(self):
            self.play_calls += 1
            self.state = self.PlaybackState.PlayingState

        def stop(self):
            self.state = self.PlaybackState.StoppedState

        def playbackState(self):
            return self.state

    monkeypatch.setattr(recording, "QAudioOutput", Output)
    monkeypatch.setattr(recording, "QMediaPlayer", Player)
    dialog = recording.RecordingDialog(mode="temporary")
    dialog.start_button.click()
    capture_env.source.instances[-1].stream.push(pcm())
    dialog.stop_button.click()
    dialog.preview_button.click()
    assert dialog._audio_output.selected.id() == b"initial-speaker"
    capture_env.devices.output = Device(b"new-headphones")
    dialog._media_devices.audioOutputsChanged.emit()
    assert dialog._audio_output.selected.id() == b"new-headphones"
    capture_env.devices.output = Device(b"")
    dialog._media_devices.audioOutputsChanged.emit()
    assert dialog._player.state == Player.PlaybackState.StoppedState
    assert "未检测到" in dialog.status_label.text()
    dialog.preview_button.click()
    assert dialog._player.play_calls == 1
    capture_env.devices.output = Device(b"restored-speaker")
    dialog.preview_button.click()
    assert dialog._player.play_calls == 2 and dialog._audio_output.selected.id() == b"restored-speaker"
    # Starting a new take clears the preview source before capture begins.
    dialog.start_button.click()
    assert dialog._player.source.isEmpty() and dialog._player.state == Player.PlaybackState.StoppedState
    dialog.reject()


@pytest.mark.parametrize("area", [QRect(-1080, 0, 720, 1232), QRect(3440, 0, 1536, 816), QRect(0, 0, 640, 360)])
def test_dialog_placement_uses_logical_screen_coordinates(area, monkeypatch):
    events, result = [], {}
    screen = SimpleNamespace(availableGeometry=lambda: area)
    handle = SimpleNamespace(setScreen=lambda value: events.append(value), frameMargins=lambda: QMargins(8, 31, 8, 8))
    window = SimpleNamespace(_closed=False, parentWidget=lambda: None, winId=lambda: 1,
                             windowHandle=lambda: handle, setMinimumSize=lambda *args: None,
                             setGeometry=lambda value: result.update(rect=value))
    monkeypatch.setattr(recording, "QGuiApplication", SimpleNamespace(primaryScreen=lambda: screen))
    recording.RecordingDialog._fit_to_screen(window)
    assert events == [screen]
    assert area.contains(result["rect"].marginsAdded(handle.frameMargins()))
    assert result["rect"].width() <= 660


def test_small_dialog_scrolls_and_first_show_timer_stops_on_close(capture_env, monkeypatch):
    original = recording.centered_client_geometry
    area = QRect(80, 40, 640, 360)
    calls = []

    def small_geometry(available, preferred, margins):
        calls.append(True)
        return original(area, preferred, margins)

    monkeypatch.setattr(recording, "centered_client_geometry", small_geometry)
    dialog = recording.RecordingDialog()
    dialog.show()
    capture_env.app.processEvents()
    assert area.contains(dialog.frameGeometry())
    assert dialog.content_scroll.verticalScrollBar().maximum() > 0
    assert dialog.buttons.y() + dialog.buttons.height() <= dialog.height()
    assert len(calls) == 2
    dialog.move(100, 100)
    dialog.hide()
    dialog.show()
    capture_env.app.processEvents()
    assert len(calls) == 2
    dialog.close()
    assert not dialog._placement_timer.isActive()
    dialog._placement_timer.timeout.emit()
    assert len(calls) == 2
