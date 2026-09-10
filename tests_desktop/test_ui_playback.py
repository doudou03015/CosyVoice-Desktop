"""Playback wiring without changing Windows devices or sending sound to them."""
import os
from pathlib import Path

if os.name == "nt":
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(3)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer as NativePlayer
from PySide6.QtWidgets import QApplication

from desktop_app import paths, ui


class Device:
    def __init__(self, name):
        self.name = name

    def id(self):
        return self.name.encode()

    def description(self):
        return self.name

    def isNull(self):
        return not self.name


class Devices(QObject):
    audioOutputsChanged = Signal()
    current = Device("初始音箱")

    @staticmethod
    def defaultAudioOutput():
        return Devices.current


class Output(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.selected = Devices.current
        self.selected_devices = []

    def device(self):
        return self.selected

    def setDevice(self, value):
        self.selected = value
        self.selected_devices.append(value)


class Player(QObject):
    Error = NativePlayer.Error
    MediaStatus = NativePlayer.MediaStatus
    PlaybackState = NativePlayer.PlaybackState
    errorOccurred = Signal(object, str)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    positionChanged = Signal(int)
    durationChanged = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.state = self.PlaybackState.StoppedState
        self.status = self.MediaStatus.NoMedia
        self.elapsed = self.length = self.play_calls = 0
        self.source = QUrl()

    def setAudioOutput(self, output):
        self.output = output

    def setSource(self, source):
        self.source = source
        self.status = self.MediaStatus.LoadedMedia
        self.mediaStatusChanged.emit(self.status)

    def play(self):
        self.play_calls += 1
        self.state = self.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self.state)

    def stop(self):
        self.state = self.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self.state)

    def playbackState(self):
        return self.state

    def mediaStatus(self):
        return self.status

    def position(self):
        return self.elapsed

    def duration(self):
        return self.length

    def errorString(self):
        return "测试解码错误"


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    Devices.current = Device("初始音箱")
    test_session = tmp_path / "session"
    test_session.mkdir()
    monkeypatch.setattr(paths, "_SESSION", test_session)
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(tmp_path / "data"))
    monkeypatch.setattr(ui, "QMediaDevices", Devices)
    monkeypatch.setattr(ui, "QAudioOutput", Output)
    monkeypatch.setattr(ui, "QMediaPlayer", Player)
    monkeypatch.setattr(ui.MainWindow, "detect_hardware", lambda self: None)
    result = ui.MainWindow()
    result.status_label.setText("配音任务：第 3 段 / 10 段")
    result.test_audio = tmp_path / "中文 试听.wav"
    result.test_audio.write_bytes(b"not decoded by this fake player")
    yield result
    result._dirty = False
    result.close()
    result.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_preview_button_rebinds_default_changed_since_window_opened(window):
    Devices.current = Device("新默认蓝牙音箱")
    window._text_output = str(window.test_audio)
    window.text_play.setEnabled(True)
    window.text_play.click()
    assert window.audio_output.selected_devices[-1] is Devices.current
    assert window.player.output is window.audio_output
    assert window.player.play_calls == 1
    assert Path(window.player.source.toLocalFile()) == window.test_audio
    assert "正在播放" in window.playback_label.text()
    assert "新默认蓝牙音箱" in window.playback_label.text()
    assert window.status_label.text() == "配音任务：第 3 段 / 10 段"


def test_device_change_signal_redirects_ongoing_preview(window):
    window.play(window.test_audio)
    Devices.current = Device("切换后的耳机")
    window.media_devices.audioOutputsChanged.emit()
    assert window.audio_output.device() is Devices.current
    assert window.player.state == Player.PlaybackState.PlayingState
    assert "切换后的耳机" in window.playback_label.text()


def test_no_output_device_prevents_play_and_recovers_after_reconnect(window):
    Devices.current = Device("")
    window.play(window.test_audio)
    assert window.player.play_calls == 0
    assert "试听失败" in window.playback_label.text()
    assert "连接音箱或耳机" in window.playback_label.text()
    assert window.status_label.text() == "配音任务：第 3 段 / 10 段"
    Devices.current = Device("重新连接的音箱")
    window.media_devices.audioOutputsChanged.emit()
    window.play(window.test_audio)
    assert window.player.play_calls == 1
    assert "试听失败" not in window.playback_label.text()


def test_playback_error_is_visible_on_current_page_and_retry_clears_it(window):
    window.play(window.test_audio)
    window.player.errorOccurred.emit(Player.Error.FormatError, "音频格式无法解码")
    assert "试听失败：音频格式无法解码" == window.playback_label.text()
    assert "音频格式无法解码" in window.logs.toPlainText()
    assert window.status_label.text() == "配音任务：第 3 段 / 10 段"
    window.play(window.test_audio)
    assert "正在播放" in window.playback_label.text()


def test_progress_stop_and_end_are_distinct_and_shared_across_pages(window):
    window.play(window.test_audio)
    window.player.length = 7000
    window.player.durationChanged.emit(7000)
    window.player.elapsed = 2500
    window.player.positionChanged.emit(2500)
    assert "00:02 / 00:07" in window.playback_label.text()
    window.navigation.setCurrentRow(1)
    assert window.playback_label.parent() is not window.stack.widget(0)
    window.stop_playback()
    assert "已停止" in window.playback_label.text()
    window.play(window.test_audio)
    window.player.elapsed = 7000
    window.player.state = Player.PlaybackState.StoppedState
    window.player.status = Player.MediaStatus.EndOfMedia
    window.player.mediaStatusChanged.emit(window.player.status)
    assert "播放完成" in window.playback_label.text()
    assert "00:07 / 00:07" in window.playback_label.text()


def test_removed_output_stops_playback_and_shows_action(window):
    window.play(window.test_audio)
    Devices.current = Device("")
    window.media_devices.audioOutputsChanged.emit()
    assert window.player.state == Player.PlaybackState.StoppedState
    assert "未检测到可用的音频输出设备" in window.playback_label.text()
