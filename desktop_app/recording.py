"""Explicit microphone capture and a shared reference-recording dialog.

Opening the dialog only enumerates input devices. QAudioSource is constructed
and started after the user presses Start. Accepted recordings remain owned by
the dialog until cleanup(); callers must copy the take_result() audio first.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
from uuid import uuid4
import wave

import numpy as np
from PySide6.QtCore import QObject, QMargins, QMicrophonePermission, QSize, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import (
    QAudioFormat, QAudioOutput, QAudioSource, QMediaDevices, QMediaPlayer, QtAudio,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .paths import outside_sync, session_dir
from .window_placement import centered_client_geometry


DEFAULT_TRANSCRIPT = (
    "大家好，欢迎收听今天的讲解。接下来，我们会按照页面顺序，"
    "介绍主要内容、关键步骤和需要注意的事项。"
)
MIN_SECONDS = 3
MAX_SECONDS = 30


def capture_format(device):
    """Prefer mono PCM; retain a supported native rate instead of resampling."""
    for rate in (24000, 48000, 44100, 16000):
        candidate = QAudioFormat()
        candidate.setSampleRate(rate)
        candidate.setChannelCount(1)
        candidate.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isFormatSupported(candidate):
            return candidate
    preferred = device.preferredFormat()
    if (preferred.sampleRate() >= 16000 and 1 <= preferred.channelCount() <= 8
            and preferred.sampleFormat() in (
                QAudioFormat.SampleFormat.UInt8, QAudioFormat.SampleFormat.Int16,
                QAudioFormat.SampleFormat.Int32, QAudioFormat.SampleFormat.Float)
            and device.isFormatSupported(preferred)):
        return preferred
    raise ValueError("这个输入设备不支持至少 16000 Hz 的录音格式，请换一个麦克风。")


class PCMCollector:
    """Bounded, frame-aligned PCM collection; independent of microphone hardware."""

    def __init__(self, audio_format):
        self.rate = audio_format.sampleRate()
        self.channels = audio_format.channelCount()
        self.sample_format = audio_format.sampleFormat()
        self.dtype = {
            QAudioFormat.SampleFormat.UInt8: np.dtype("u1"),
            QAudioFormat.SampleFormat.Int16: np.dtype("<i2"),
            QAudioFormat.SampleFormat.Int32: np.dtype("<i4"),
            QAudioFormat.SampleFormat.Float: np.dtype("<f4"),
        }.get(self.sample_format)
        if self.rate < 16000 or self.channels < 1 or self.dtype is None:
            raise ValueError("麦克风返回了不支持的音频格式。")
        self.frame_bytes = self.dtype.itemsize * self.channels
        self.limit = int(MAX_SECONDS * self.rate)
        self.frames = 0
        self.peak = 0.0
        self.clipped = False
        self._remainder = b""
        self._parts = []

    @property
    def duration(self):
        return self.frames / self.rate

    def feed(self, raw):
        """Return True at exactly 30 seconds, even when a read overruns the limit."""
        if self.frames >= self.limit:
            return True
        combined = self._remainder + bytes(raw)
        available = len(combined) // self.frame_bytes
        count = min(available, self.limit - self.frames)
        consumed = count * self.frame_bytes
        self._remainder = combined[available * self.frame_bytes:]
        if not count:
            return False
        samples = np.frombuffer(combined[:consumed], dtype=self.dtype).reshape(-1, self.channels)
        if self.sample_format == QAudioFormat.SampleFormat.UInt8:
            samples = (samples.astype(np.float32) - 128) / 128
        elif self.sample_format == QAudioFormat.SampleFormat.Int16:
            samples = samples.astype(np.float32) / 32768
        elif self.sample_format == QAudioFormat.SampleFormat.Int32:
            samples = samples.astype(np.float64) / 2147483648
        if not np.isfinite(samples).all():
            raise ValueError("麦克风返回了无效音频，请换一个设备后重录。")
        mono = samples.mean(axis=1, dtype=np.float64)
        self.peak = float(np.max(np.abs(mono)))
        self.clipped = self.clipped or self.peak >= 0.999
        # PCM16 is the final format. Validation uses the actual quantized signal.
        pcm = np.rint(np.clip(mono, -1, 32767 / 32768) * 32768).astype("<i2")
        self._parts.append(pcm.tobytes())
        self.frames += count
        return self.frames >= self.limit

    def validated_pcm(self):
        if self.frames < int(MIN_SECONDS * self.rate):
            raise ValueError("录音不足 3 秒，请完整朗读一段文字后重录。")
        pcm = b"".join(self._parts)
        values = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
        # A disconnected driver can return a nonzero constant/DC signal.
        # Measure the varying part, without altering the saved samples.
        centered = values - values.mean()
        rms = float(np.sqrt(np.mean(centered * centered)))
        if rms < 1e-5:
            raise ValueError("没有录到清晰声音，录音为静音或音量过低，请检查麦克风后重录。")
        return pcm


class AudioRecorder(QObject):
    """Single capture owner. Signals and readyRead handlers run on the UI thread."""

    started = Signal()
    progress = Signal(float, float)  # captured seconds, peak level in [0, 1]
    finished = Signal(str, float)  # absolute mono PCM16 WAV path, seconds
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self._source = None
        self._stream = None
        self._collector = None
        self._device_id = None
        self._audio_path = None
        self._starting = False
        self._last_data_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._check_input)
        self._devices = QMediaDevices(self)
        self._devices.audioInputsChanged.connect(self._devices_changed)

    def start(self, device=None):
        if self.active:
            return
        self.cleanup()
        device = device if device is not None else QMediaDevices.defaultAudioInput()
        try:
            if device.isNull():
                raise ValueError("没有可用的麦克风，请连接设备并检查 Windows 的麦克风权限。")
            self._device_id = bytes(device.id())
            if self._device_id not in {bytes(item.id()) for item in QMediaDevices.audioInputs()}:
                raise ValueError("所选麦克风已断开，请重新选择设备。")
            audio_format = capture_format(device)
            self._collector = PCMCollector(audio_format)
            self._source = QAudioSource(device, audio_format, self)
            self._source.setBufferSize(max(4096, int(audio_format.bytesPerFrame() * audio_format.sampleRate() / 10)))
            self._source.stateChanged.connect(self._state_changed)
            self.active = True
            self._starting = True
            try:
                self._stream = self._source.start()
            finally:
                self._starting = False
            if self._stream is None or self._source.error() != QtAudio.Error.NoError:
                raise ValueError("无法打开麦克风，请检查 Windows 麦克风权限，或关闭独占此设备的其他程序。")
            self._stream.readyRead.connect(self._read_available)
            self._last_data_at = time.monotonic()
            self._timer.start()
            self.started.emit()
            self._read_available()
        except Exception as error:
            self._fail(str(error))

    def _read_available(self):
        if not self.active or self._stream is None:
            return
        try:
            raw = bytes(self._stream.readAll())
            if not raw:
                return
            self._last_data_at = time.monotonic()
            reached_limit = self._collector.feed(raw)
            self.progress.emit(self._collector.duration, min(1.0, self._collector.peak))
            if reached_limit:
                self.stop(drain=False)
        except Exception as error:
            self._fail(str(error))

    def _release_input(self):
        self.active = False
        self._timer.stop()
        stream, source = self._stream, self._source
        self._stream = self._source = None
        if stream is not None:
            try:
                stream.readyRead.disconnect(self._read_available)
            except (RuntimeError, TypeError):
                pass
        if source is not None:
            try:
                source.stateChanged.disconnect(self._state_changed)
            except (RuntimeError, TypeError):
                pass
            try:
                source.stop()
            finally:
                source.deleteLater()

    def stop(self, drain=True):
        if not self.active:
            return
        # Include the final complete frames already delivered by the device.
        if drain:
            self._read_available()
            if not self.active:
                return  # readAll may reach the sample limit and finish itself.
        collector = self._collector
        self._release_input()
        pending = None
        try:
            pcm = collector.validated_pcm()
            target = outside_sync(session_dir() / ("recording-" + uuid4().hex + ".wav"))
            pending = target.with_suffix(".part.wav")
            with wave.open(str(pending), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(collector.rate)
                output.writeframes(pcm)
            pending.replace(target)
            self._audio_path = target
            self.finished.emit(str(target), collector.duration)
        except Exception as error:
            if pending is not None:
                pending.unlink(missing_ok=True)
            self._fail(str(error))
        finally:
            self._collector = None

    def _state_changed(self, state):
        if not self.active or self._starting or self._source is None:
            return
        if self._source.error() != QtAudio.Error.NoError:
            self._fail("麦克风录音中断，请检查设备连接和 Windows 麦克风权限后重录。")
        elif state == QtAudio.State.StoppedState:
            self._fail("麦克风意外停止，本段录音未保存，请重新录制。")

    def _devices_changed(self):
        if self.active and self._device_id not in {bytes(item.id()) for item in QMediaDevices.audioInputs()}:
            self._fail("所选麦克风已断开，本段录音未保存，请连接后重录。")

    def _check_input(self):
        if self.active and time.monotonic() - self._last_data_at > 5:
            self._fail("麦克风持续没有返回音频，请检查设备和权限后重录。")

    def _fail(self, message):
        self.cleanup()
        self.failed.emit(message)

    def cleanup(self):
        self._release_input()
        self._collector = None
        if self._audio_path is not None:
            # Only the exact UUID-named output owned by this recorder is removed.
            self._audio_path.unlink(missing_ok=True)
            self._audio_path = None


class RecordingDialog(QDialog):
    """Return audio/name/transcript after explicit acceptance; no ASR or trimming."""

    def __init__(self, parent=None, mode="library", stop_playback=None):
        super().__init__(parent)
        if mode not in ("library", "temporary"):
            raise ValueError("未知的录音模式。")
        self.mode = mode
        self.stop_playback = stop_playback
        self.result_voice = None
        self._closed = False
        self._placed = False
        self._placement_timer = QTimer(self)
        self._placement_timer.setSingleShot(True)
        self._placement_timer.timeout.connect(self._fit_to_screen)
        self._permission_pending = False
        self._recorded_audio = ""
        self._player = None
        self._audio_output = None
        self.recorder = AudioRecorder(self)
        self.recorder.started.connect(self._recording_started)
        self.recorder.progress.connect(self._recording_progress)
        self.recorder.finished.connect(self._recording_finished)
        self.recorder.failed.connect(self._recording_failed)
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioInputsChanged.connect(self._refresh_devices)
        self._media_devices.audioOutputsChanged.connect(self._sync_preview_device)

        self.setWindowTitle("录制新音色" if mode == "library" else "录制临时参考音频")
        self.resize(660, 540)
        outer_layout = QVBoxLayout(self)
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.content_scroll.setWidget(body)
        outer_layout.addWidget(self.content_scroll, 1)
        tips = QLabel("按稿朗读，录音须为 3～30 秒，建议 3～15 秒。请在安静环境下录制，避免背景音乐。")
        tips.setWordWrap(True)
        layout.addWidget(tips)
        form = QFormLayout()
        self.device_combo = QComboBox()
        form.addRow("麦克风", self.device_combo)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：我的讲解声音")
        if mode == "library":
            form.addRow("音色名称", self.name_edit)
        else:
            self.name_edit.setText("临时参考录音")
            self.name_edit.hide()
        layout.addLayout(form)
        layout.addWidget(QLabel("朗读稿（可先修改；录完后请核对是否与实际说出的文字一致）"))
        self.transcript_edit = QPlainTextEdit(DEFAULT_TRANSCRIPT)
        self.transcript_edit.setMinimumHeight(120)
        layout.addWidget(self.transcript_edit)
        self.time_label = QLabel("已录制 0.0 秒 / 30 秒")
        self.level_meter = QProgressBar()
        self.level_meter.setRange(0, 100)
        self.level_meter.setValue(0)
        self.level_meter.setTextVisible(False)
        self.level_meter.setAccessibleName("麦克风音量")
        layout.addWidget(self.time_label)
        layout.addWidget(self.level_meter)
        actions = QHBoxLayout()
        self.start_button = QPushButton("开始录音")
        self.stop_button = QPushButton("停止录音")
        self.preview_button = QPushButton("试听录音")
        for widget in (self.start_button, self.stop_button, self.preview_button):
            widget.setAutoDefault(False)
            actions.addWidget(widget)
        self.start_button.clicked.connect(self._start_requested)
        self.stop_button.clicked.connect(lambda: self.recorder.stop())
        self.preview_button.clicked.connect(self._preview)
        layout.addLayout(actions)
        self.status_label = QLabel("点击“开始录音”后才会打开麦克风。录音不会自动上传。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("保存音色" if mode == "library" else "使用本次录音")
        self.save_button.setAutoDefault(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer_layout.addWidget(self.buttons)
        self._refresh_devices()
        self._set_recording_ui(False)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._placed:
            self._placed = True
            self._fit_to_screen()
            # Frame decorations may only become accurate after the first show.
            self._placement_timer.start(0)

    def _fit_to_screen(self):
        if self._closed:
            return
        parent = self.parentWidget()
        screen = parent.window().screen() if parent is not None else QGuiApplication.primaryScreen()
        if screen is None:
            return
        self.winId()
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)
        margins = handle.frameMargins() if handle is not None else QMargins()
        available = screen.availableGeometry()
        geometry = centered_client_geometry(available, QSize(660, 540), margins)
        if parent is not None:
            frame = geometry.marginsAdded(margins)
            frame.moveCenter(parent.window().frameGeometry().center())
            frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
            frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
            geometry = frame.marginsRemoved(margins)
        self.setMinimumSize(0, 0)
        self.setGeometry(geometry)

    def _refresh_devices(self):
        if self.recorder.active or self._permission_pending:
            return
        selected = self.device_combo.currentData()
        selected_id = bytes(selected.id()) if selected is not None else None
        self.device_combo.clear()
        self.device_combo.addItem("系统默认输入设备", None)
        for device in QMediaDevices.audioInputs():
            self.device_combo.addItem(device.description(), device)
            if selected_id == bytes(device.id()):
                self.device_combo.setCurrentIndex(self.device_combo.count() - 1)

    def _set_recording_ui(self, recording):
        self.device_combo.setEnabled(not recording)
        self.transcript_edit.setReadOnly(recording)
        self.start_button.setEnabled(not recording)
        self.stop_button.setEnabled(recording and not self._permission_pending)
        self.preview_button.setEnabled(not recording and bool(self._recorded_audio))
        self.save_button.setEnabled(not recording and bool(self._recorded_audio))

    def _permission_status(self):
        return QApplication.instance().checkPermission(QMicrophonePermission())

    def _start_requested(self):
        if self.recorder.active or self._permission_pending or self._closed:
            return
        if not self.transcript_edit.toPlainText().strip():
            self.status_label.setText("请先填写准备朗读的文字。")
            return
        self._stop_preview()
        try:
            if self.stop_playback is not None:
                self.stop_playback()
            status = self._permission_status()
            if status == Qt.PermissionStatus.Denied:
                self.status_label.setText("麦克风权限未开启，请在 Windows 设置的“隐私与安全性 → 麦克风”中允许桌面应用访问。")
                return
            if status == Qt.PermissionStatus.Undetermined:
                self._permission_pending = True
                self._set_recording_ui(True)
                self.status_label.setText("请允许使用麦克风；允许后开始本次录音。")
                QApplication.instance().requestPermission(QMicrophonePermission(), self, self._permission_ready)
                return
            self._begin_capture()
        except Exception as error:
            self._permission_pending = False
            self._recording_failed("无法开始录音：" + str(error))

    def _permission_ready(self, permission):
        if self._closed or not self._permission_pending:
            return
        self._permission_pending = False
        if permission.status() != Qt.PermissionStatus.Granted:
            self._set_recording_ui(False)
            self.status_label.setText("麦克风权限未获允许，请开启权限后再录制。")
            return
        self._begin_capture()

    def _begin_capture(self):
        self._recorded_audio = ""
        self.result_voice = None
        self.time_label.setText("已录制 0.0 秒 / 30 秒")
        self.level_meter.setValue(0)
        self._set_recording_ui(True)
        self.status_label.setText("正在打开麦克风……")
        self.recorder.start(self.device_combo.currentData())

    def _recording_started(self):
        self._set_recording_ui(True)
        self.start_button.setText("重新录音")
        self.status_label.setText("正在录音，请按稿朗读。达到 30 秒会自动停止。")

    def _recording_progress(self, seconds, peak):
        self.time_label.setText(f"已录制 {seconds:.1f} 秒 / 30 秒")
        # Logarithmic display makes normal speech visible without changing audio.
        decibels = 20 * math.log10(max(peak, 1e-6))
        self.level_meter.setValue(round(max(0, min(100, (decibels + 60) / 60 * 100))))

    def _recording_finished(self, path, seconds):
        self._recorded_audio = path
        self.time_label.setText(f"已录制 {seconds:.1f} 秒 / 30 秒")
        self.level_meter.setValue(0)
        self._set_recording_ui(False)
        prefix = "已到 30 秒，录音自动停止。" if seconds >= MAX_SECONDS else "录音已停止。"
        self.status_label.setText(prefix + "请试听，并核对原文后保存；如果没有读完，请修改原文或重新录制。")
        self._refresh_devices()

    def _recording_failed(self, message):
        self._recorded_audio = ""
        self.result_voice = None
        self.level_meter.setValue(0)
        self._set_recording_ui(False)
        self.status_label.setText(message)
        self._refresh_devices()

    def _preview(self):
        if self.recorder.active or not self._recorded_audio:
            return
        if self._player is not None and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_preview()
            return
        if self.stop_playback is not None:
            self.stop_playback()
        if QMediaDevices.defaultAudioOutput().isNull():
            self.status_label.setText("未检测到可用的音频输出设备，请连接音箱或耳机后重试。")
            return
        if self._player is None:
            self._audio_output = QAudioOutput(self)
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.playbackStateChanged.connect(self._playback_state_changed)
            self._player.errorOccurred.connect(self._playback_error)
        if not self._sync_preview_device(force=True):
            return
        self._player.setSource(QUrl.fromLocalFile(self._recorded_audio))
        self._player.play()

    def _sync_preview_device(self, force=False):
        if self._player is None or self._closed:
            return False
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self._stop_preview()
            self.status_label.setText("未检测到可用的音频输出设备，请连接音箱或耳机后重试。")
            return False
        if force or bytes(self._audio_output.device().id()) != bytes(device.id()):
            self._audio_output.setDevice(device)
        return True

    def _playback_state_changed(self, state):
        self.preview_button.setText("停止试听" if state == QMediaPlayer.PlaybackState.PlayingState else "试听录音")

    def _playback_error(self, error, message):
        self.status_label.setText("试听失败，请检查扬声器或耳机。" + str(message))

    def _stop_preview(self):
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self.preview_button.setText("试听录音")

    def accept(self):
        if self.recorder.active or self._permission_pending or not self._recorded_audio:
            return
        name = self.name_edit.text().strip()
        transcript = self.transcript_edit.toPlainText().strip()
        if not name:
            self.status_label.setText("请填写音色名称。")
            return
        try:
            from .voices import validate_reference
            stats = validate_reference(self._recorded_audio, transcript)
            if not MIN_SECONDS <= stats["duration"] <= MAX_SECONDS:
                raise ValueError("录音须为 3～30 秒，请重新录制。")
        except (ValueError, OSError, RuntimeError) as error:
            self.status_label.setText(str(error))
            return
        self.result_voice = dict(audio=str(Path(self._recorded_audio).resolve()), transcript=transcript, name=name)
        self.done(QDialog.DialogCode.Accepted)

    def take_result(self):
        """Return a copy; this does not transfer file ownership or remove audio."""
        if self.result() != QDialog.DialogCode.Accepted or self.result_voice is None:
            raise ValueError("录音尚未确认保存。")
        return dict(self.result_voice)

    def done(self, result):
        self._closed = True
        self._placement_timer.stop()
        self._permission_pending = False
        self._stop_preview()
        if result != QDialog.DialogCode.Accepted:
            self.cleanup()
        super().done(result)

    def cleanup(self):
        """Release capture/playback and remove this dialog's temporary WAV only."""
        self._permission_pending = False
        self._stop_preview()
        self.recorder.cleanup()
        self._recorded_audio = ""
        self.result_voice = None
