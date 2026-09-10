"""Chinese desktop workspace. Inference lives exclusively in a child process."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import traceback
from uuid import uuid4

from PySide6.QtCore import QProcess, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .paths import app_root, atomic_json, load_settings, save_settings, session_dir, user_data_dir
from .qt_worker import InferenceProcess
from .window_placement import place_on_primary_screen
from . import documents, media, projects, voices


STYLE = """
QMainWindow, QWidget#workspace { background: #f4f6fa; color: #1f2937; }
QLabel { color: #243246; }
QLabel#title { font-size: 26px; font-weight: 700; color: #18263a; }
QLabel#subtitle { color: #68768b; font-size: 12px; }
QLabel#brand { color: white; font-size: 19px; font-weight: 700; padding: 0; }
QLabel#brandNote { color: #9fb2c9; padding: 0px 16px 16px; }
QFrame#sidebar { background: #142238; border: none; }
QListWidget#navigation { background: transparent; border: none; color: #aebece; font-size: 14px; }
QListWidget#navigation::item { padding: 16px 14px; margin: 3px 10px; border-radius: 7px; }
QListWidget#navigation::item:selected { background: #274969; color: white; }
QListWidget#navigation::item:hover { background: #203650; }
QGroupBox { background: white; border: 1px solid #dfe5ee; border-radius: 9px; margin-top: 18px; padding: 17px 13px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
QPushButton { background: white; color: #263952; border: 1px solid #ccd5e2; border-radius: 6px; padding: 8px 13px; min-height: 19px; }
QPushButton:hover { background: #eaf1fa; border-color: #98b4d4; }
QPushButton:disabled { color: #a0abba; background: #f0f2f6; border-color: #e0e5ed; }
QPushButton[primary="true"] { background: #2563a6; border-color: #2563a6; color: white; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #1d528c; }
QPushButton[primary="true"]:disabled { background: #aebed0; border-color: #aebed0; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; color: #23344b; border: 1px solid #cfd8e5; border-radius: 5px; padding: 6px; selection-background-color: #316cad; }
QPlainTextEdit:focus, QLineEdit:focus { border: 1px solid #4e8ac5; }
QListWidget, QTableWidget { background: white; border: 1px solid #dfe5ee; border-radius: 6px; }
QListWidget::item { padding: 8px; }
QListWidget::item:selected { background: #e2edfa; color: #174573; }
QHeaderView::section { background: #edf2f8; border: none; padding: 8px; color: #47607e; }
QProgressBar { border: none; background: #e4eaf2; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #2b7bc0; border-radius: 4px; }
QCheckBox { spacing: 6px; }
QSplitter::handle { background: #e5eaf1; }
"""


def button(text, callback=None, primary=False):
    result = QPushButton(text)
    result.setProperty("primary", primary)
    if callback:
        result.clicked.connect(callback)
    return result


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(9)
    for widget in widgets:
        if isinstance(widget, int):
            layout.addStretch(widget)
        else:
            layout.addWidget(widget)
    return layout


def note(text):
    label = QLabel(text)
    label.setObjectName("subtitle")
    label.setWordWrap(True)
    return label


def spin(value=1.0, minimum=0.5, maximum=2.0, step=0.1):
    result = QDoubleSpinBox()
    result.setRange(minimum, maximum)
    result.setSingleStep(step)
    result.setDecimals(2)
    result.setValue(value)
    return result


class BackgroundTask(QThread):
    progress = Signal(dict)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.function(self.progress.emit, self.cancel_event.is_set)
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class VoiceSelector(QGroupBox):
    changed = Signal()

    def __init__(self, owner, title="配音声音", project_context=False):
        super().__init__(title)
        self.owner = owner
        self.project_context = project_context
        self._temporary_key = None
        self._temporary_voice = None
        layout = QVBoxLayout(self)
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(18)
        layout.addLayout(row(self.combo, button("试听参考", self.preview)))
        self.temporary = QWidget()
        form = QFormLayout(self.temporary)
        form.setContentsMargins(0, 3, 0, 0)
        self.audio = QLineEdit()
        self.audio.setPlaceholderText("选择一段 3～15 秒的清晰录音")
        picker = QWidget()
        picker.setLayout(row(self.audio, button("选择录音", self.choose_audio)))
        form.addRow("参考录音", picker)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText("填写参考录音中实际说出的完整原文")
        self.transcript.setMaximumHeight(90)
        form.addRow("录音原文", self.transcript)
        layout.addWidget(self.temporary)
        self.detail = note("")
        layout.addWidget(self.detail)
        self.combo.currentIndexChanged.connect(self._changed)
        self.audio.textChanged.connect(self.changed)
        self.transcript.textChanged.connect(self.changed)
        self.refresh()

    def refresh(self):
        selected = self.combo.currentData()
        self.combo.blockSignals(True)
        self.combo.clear()
        for voice in self.owner.voice_items:
            self.combo.addItem(voice["name"], voice["id"])
        self.combo.addItem("＋ 临时参考录音（本次使用）", "__temporary__")
        index = self.combo.findData(selected)
        self.combo.setCurrentIndex(max(0, index))
        self.combo.blockSignals(False)
        self._changed()

    def _changed(self):
        temporary = self.combo.currentData() == "__temporary__"
        self.temporary.setVisible(temporary)
        item = self.owner.voice_by_id(self.combo.currentData())
        self.detail.setText("无需加入音色库；保存工程时会一并保留录音。" if temporary else
                            ((item or {}).get("source", "") + "  " + (item or {}).get("license", "")))
        self.changed.emit()

    def current(self):
        if self.combo.currentData() == "__temporary__":
            audio, transcript = self.audio.text().strip(), self.transcript.toPlainText().strip()
            source = Path(audio)
            stamp = (source.stat().st_size, source.stat().st_mtime_ns) if source.is_file() else (0, 0)
            key = audio, transcript, stamp
            if key != self._temporary_key:
                self._temporary_voice = voices.temporary_voice(audio, transcript)
                self._temporary_key = key
            return dict(self._temporary_voice)
        item = (self.owner.project_voice_by_id(self.combo.currentData()) if self.project_context else
                self.owner.voice_by_id(self.combo.currentData()))
        if not item:
            raise ValueError("请先选择一个音色。")
        return dict(item)

    def select(self, voice_id):
        index = self.combo.findData(voice_id)
        if index >= 0:
            self.combo.setCurrentIndex(index)

    def choose_audio(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择参考录音", "", "音频 (*.wav *.flac *.mp3 *.ogg);;所有文件 (*)")
        if filename:
            self.audio.setText(filename)

    def preview(self):
        try:
            self.owner.play(self.current()["audio"])
        except Exception as exc:
            self.owner.show_error(str(exc))


class MainWindow(QMainWindow):
    hardware_ready = Signal()
    verification_finished = Signal(dict)

    def __init__(self, verification=False):
        super().__init__()
        self._verification_mode = verification
        self.setWindowTitle("CosyVoice 配音工作台")
        self.setWindowIcon(QIcon(str(app_root() / "desktop_app" / "assets" / "app.ico")))
        self.resize(1280, 850)
        self.setMinimumSize(1060, 740)
        self._initial_placement_prepared = False
        self._initial_placement_finished = False
        self.setStyleSheet(STYLE)
        self.settings = load_settings()
        self.library = voices.VoiceLibrary()
        self.voice_items = self.library.list()
        self.project = None
        self.project_dir = None
        self.current_slide = None
        self._updating = False
        self._dirty = False
        self._background = None
        self._background_success = None
        self._background_error = None
        self._batch = []
        self._batch_total = 0
        self._batch_completed = 0
        self._batch_export = None
        self._active_job = None
        self._text_output = None
        self._gpu_items = []
        self._last_gpu_probe = 0.0
        self._pending_action = None
        self._self_test_signature = ""
        self._playback_path = ""
        self._playback_error = ""
        self._playback_stopped_by_user = False
        self.worker = InferenceProcess(self)
        self.worker.configure(self.settings)
        self.worker.event.connect(self._worker_event)
        self.worker.log.connect(self.append_log)
        self.worker.busy_changed.connect(self.update_busy)
        self.audio_output = QAudioOutput(self)
        self.media_devices = QMediaDevices(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.errorOccurred.connect(self._playback_failed)
        self.player.playbackStateChanged.connect(self._update_playback_status)
        self.player.mediaStatusChanged.connect(self._update_playback_status)
        self.player.positionChanged.connect(self._update_playback_status)
        self.player.durationChanged.connect(self._update_playback_status)
        self._build()
        self.media_devices.audioOutputsChanged.connect(self._audio_outputs_changed)
        self._sync_audio_device()
        self._restore_text_state()
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.timeout.connect(self._save_text_state)
        for signal in (self.text_edit.textChanged, self.text_voice.changed, self.text_speed.valueChanged, self.text_seed.valueChanged):
            signal.connect(lambda *args: self._draft_timer.start(800))
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self.detect_hardware)
        self._startup_timer.start(150)
        self._placement_timer = QTimer(self)
        self._placement_timer.setSingleShot(True)
        self._placement_timer.timeout.connect(self._finish_initial_placement)

    def prepare_initial_placement(self):
        """Called by the entry point before the window is first displayed."""
        if self._initial_placement_finished:
            return
        place_on_primary_screen(self)
        self._initial_placement_prepared = True

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_placement_finished:
            if not self._initial_placement_prepared:
                self.prepare_initial_placement()
            self._placement_timer.start(0)

    def _finish_initial_placement(self):
        if self._initial_placement_finished:
            return
        # Windows now knows the real title bar and resize frame dimensions.
        place_on_primary_screen(self)
        self._initial_placement_finished = True

    def _build(self):
        central = QWidget()
        central.setObjectName("workspace")
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(215)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 16)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(16, 22, 12, 10)
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon.setFixedSize(48, 48)
        brand_icon.setScaledContents(True)
        brand_icon.setPixmap(self.windowIcon().pixmap(96, 96))
        brand_row.addWidget(brand_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QLabel("CosyVoice\n配音工作台")
        brand.setObjectName("brand")
        brand_row.addWidget(brand, 1, Qt.AlignmentFlag.AlignVCenter)
        side.addLayout(brand_row)
        small = QLabel("本地生成 · 声音由你选择")
        small.setObjectName("brandNote")
        side.addWidget(small)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(["01   文本配音", "02   PPT 讲解视频", "03   音色库", "04   设置"])
        side.addWidget(self.navigation, 1)
        foot = QLabel("CosyVoice 3 · 0.5B\nWindows 桌面版  α")
        foot.setObjectName("brandNote")
        side.addWidget(foot)
        outer.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(25, 22, 25, 16)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._text_page())
        self.stack.addWidget(self._ppt_page())
        self.stack.addWidget(self._voices_page())
        self.stack.addWidget(self._settings_page())
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.viewport().setAutoFillBackground(False)
        self.page_scroll.setWidget(self.stack)
        self.stack.setAutoFillBackground(False)
        main.addWidget(self.page_scroll, 1)
        self.playback_label = QLabel("试听：尚未播放。")
        self.playback_label.setObjectName("playbackStatus")
        self.playback_label.setWordWrap(True)
        self.playback_label.setTextFormat(Qt.TextFormat.PlainText)
        main.addWidget(self.playback_label)
        self.status_label = QLabel("准备就绪。首次生成将加载模型。")
        self.status_label.setWordWrap(True)
        self.cancel_button = button("取消任务", self.cancel_task)
        self.cancel_button.setEnabled(False)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.cancel_button)
        main.addLayout(status_row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        main.addWidget(self.progress_bar)
        outer.addLayout(main, 1)
        self.setCentralWidget(central)
        self._refresh_voice_list()

    def _page(self, title, subtitle):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("title")
        layout.addWidget(heading)
        layout.addWidget(note(subtitle))
        return content, layout

    def _text_page(self):
        page, layout = self._page("让文字变成声音", "粘贴讲稿、选择声音，在本机生成可试听和导出的配音。")
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addLayout(row(QLabel("待配音文字"), 1, button("导入 TXT / DOCX", self.import_text)))
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("在这里输入或粘贴内容……\n\n较长的讲稿会自动分段生成，完成后合并成一条音频。")
        self.text_edit.setObjectName("narrationText")
        left_layout.addWidget(self.text_edit, 1)
        self.text_count = note("0 字")
        self.text_edit.textChanged.connect(lambda: self.text_count.setText(f"{len(self.text_edit.toPlainText())} 字"))
        left_layout.addWidget(self.text_count)
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.text_voice = VoiceSelector(self)
        right_layout.addWidget(self.text_voice)
        options = QGroupBox("生成参数")
        form = QFormLayout(options)
        self.text_speed = spin()
        self.text_seed = QSpinBox()
        self.text_seed.setRange(0, 2_147_483_647)
        form.addRow("语速", self.text_speed)
        self.advanced = QCheckBox("高级设置")
        form.addRow(self.advanced)
        self.text_seed.setVisible(False)
        self.seed_label = QLabel("随机种子")
        self.seed_label.setVisible(False)
        form.addRow(self.seed_label, self.text_seed)
        self.advanced.toggled.connect(self.text_seed.setVisible)
        self.advanced.toggled.connect(self.seed_label.setVisible)
        right_layout.addWidget(options)
        self.text_generate = button("生成语音", self.generate_text, True)
        self.text_generate.setObjectName("generateText")
        right_layout.addWidget(self.text_generate)
        result = QGroupBox("生成结果")
        result_layout = QVBoxLayout(result)
        self.text_result = note("生成完成后，可直接试听或导出。")
        result_layout.addWidget(self.text_result)
        self.text_play = button("试听", lambda: self.play(self._text_output))
        self.text_wav = button("导出 WAV", lambda: self.export_text("wav"))
        self.text_mp3 = button("导出 MP3", lambda: self.export_text("mp3"))
        for item in (self.text_play, self.text_wav, self.text_mp3):
            item.setEnabled(False)
        result_layout.addLayout(row(self.text_play, button("停止", self.stop_playback)))
        result_layout.addLayout(row(self.text_wav, self.text_mp3))
        result_layout.addWidget(button("打开输出文件夹", self.open_outputs))
        right_layout.addWidget(result)
        right_layout.addStretch()
        split.addWidget(right)
        split.setSizes([620, 340])
        layout.addWidget(split, 1)
        return page

    def _ppt_page(self):
        page, layout = self._page("制作 PPT 讲解视频", "读取每页备注，逐页编辑配音，输出静态页面讲解视频。页面渲染需要 Microsoft PowerPoint。")
        self.new_project_button = button("导入 PPTX", self.new_project, True)
        self.open_project_button = button("打开工程", self.open_project)
        self.save_project_button = button("保存工程", self.save_project)
        layout.addLayout(row(self.new_project_button, self.open_project_button, self.save_project_button, 1,
                             button("讲稿模板", self.save_script_template)))
        self.project_title = note("尚未导入 PPT。每页讲稿和生成结果会保存在独立工程文件夹中。")
        layout.addWidget(self.project_title)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.slide_list = QListWidget()
        self.slide_list.setMinimumWidth(275)
        self.slide_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.slide_list.setWordWrap(True)
        self.slide_list.setIconSize(__import__("PySide6.QtCore", fromlist=["QSize"]).QSize(90, 51))
        self.slide_list.currentRowChanged.connect(self.select_slide)
        self.slide_list.itemChanged.connect(self._slide_checked)
        split.addWidget(self.slide_list)
        editor = QWidget()
        edit = QVBoxLayout(editor)
        edit.setContentsMargins(10, 0, 0, 0)
        top = QHBoxLayout()
        self.slide_preview = QLabel("页面预览")
        self.slide_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.slide_preview.setMinimumSize(230, 125)
        self.slide_preview.setMaximumHeight(165)
        self.slide_preview.setStyleSheet("background: #e8edf5; border-radius: 6px; color:#728197;")
        top.addWidget(self.slide_preview, 1)
        controls = QFormLayout()
        self.page_voice = QComboBox()
        self.page_speed_follow = QCheckBox("跟随全局语速")
        self.page_speed_follow.setChecked(True)
        self.page_speed = spin()
        self.page_speed.setEnabled(False)
        self.page_blank = spin(3, 0.1, 600, 0.5)
        self.page_blank.setSuffix(" 秒")
        controls.addRow("本页音色", self.page_voice)
        controls.addRow(self.page_speed_follow, self.page_speed)
        controls.addRow("无讲稿停留", self.page_blank)
        top.addLayout(controls, 1)
        edit.addLayout(top)
        self.page_label = QLabel("选择一页开始编辑")
        edit.addLayout(row(self.page_label, 1, button("导入外部讲稿", self.import_script)))
        self.page_text = QPlainTextEdit()
        self.page_text.setPlaceholderText("本页的讲稿。留空时，页面将静音停留指定秒数。")
        edit.addWidget(self.page_text, 1)
        self.page_generate = button("重新生成本页", self.generate_page)
        self.page_play = button("试听本页", self.play_page)
        edit.addLayout(row(self.page_generate, self.page_play, button("停止", self.stop_playback), 1))
        for item in (self.page_text.textChanged, self.page_voice.currentIndexChanged, self.page_speed.valueChanged,
                     self.page_blank.valueChanged, self.page_speed_follow.toggled):
            item.connect(self._page_changed)
        self.page_speed_follow.toggled.connect(lambda checked: self.page_speed.setEnabled(not checked))
        split.addWidget(editor)
        split.setSizes([300, 640])
        layout.addWidget(split, 1)
        global_box = QWidget()
        globals_layout = QHBoxLayout(global_box)
        globals_layout.setContentsMargins(0, 0, 0, 0)
        self.ppt_voice = VoiceSelector(self, "全局音色", project_context=True)
        globals_layout.addWidget(self.ppt_voice, 2)
        options = QGroupBox("视频参数")
        form = QFormLayout(options)
        self.ppt_speed = spin()
        self.ppt_tail = spin(float(self.settings.get("tail_silence", .5)), 0, 20, .1)
        self.ppt_tail.setSuffix(" 秒")
        form.addRow("全局语速", self.ppt_speed)
        form.addRow("页尾停顿", self.ppt_tail)
        form.addRow(note("1080p · 30 fps · MP4\n页面保持原比例"))
        globals_layout.addWidget(options, 1)
        layout.addWidget(global_box)
        self.ppt_voice.changed.connect(self._global_changed)
        self.ppt_speed.valueChanged.connect(self._global_changed)
        self.ppt_tail.valueChanged.connect(self._global_changed)
        self.batch_button = button("生成全部配音", self.generate_all)
        self.video_button = button("生成并导出 MP4", self.generate_video, True)
        layout.addLayout(row(self.batch_button, self.video_button, button("打开工程文件夹", self.open_project_folder), 1))
        self._refresh_page_voices()
        return page

    def _voices_page(self):
        page, layout = self._page("你的声音收藏", "预设参考样本、自建音色与临时录音，共用同一个本地配音引擎。")
        layout.addLayout(row(button("＋ 导入自建音色", self.add_voice, True), button("改名", self.rename_voice),
                             button("删除自建音色", self.delete_voice), 1))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.voice_list = QListWidget()
        self.voice_list.currentRowChanged.connect(self.show_voice)
        split.addWidget(self.voice_list)
        detail = QGroupBox("音色详情")
        form = QVBoxLayout(detail)
        self.voice_name = QLabel("")
        self.voice_name.setStyleSheet("font-size: 18px; font-weight: 600;")
        form.addWidget(self.voice_name)
        self.voice_source = note("")
        self.voice_source.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.voice_source.setOpenExternalLinks(True)
        form.addWidget(self.voice_source)
        form.addWidget(QLabel("参考录音的原文"))
        self.voice_text = QPlainTextEdit()
        self.voice_text.setReadOnly(True)
        form.addWidget(self.voice_text, 1)
        form.addLayout(row(button("参考录音", self.preview_library_voice),
                           button("合成示例", lambda: self.preview_library_voice(True)),
                           button("停止", self.stop_playback)))
        form.addWidget(button("用于文本配音", self.use_library_voice, True))
        form.addWidget(note("预设库包含 6 段参考样本，不代表 6 位不同说话人。男声、女声标签来自 FLEURS 原始元数据。"))
        split.addWidget(detail)
        split.setSizes([360, 610])
        layout.addWidget(split, 1)
        return page

    def _settings_page(self):
        page, layout = self._page("运行设置", "首次使用时准备组件和模型。设置完成后，文本配音可离线运行。")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 5, 0)
        hardware = QGroupBox("显卡与环境")
        form = QFormLayout(hardware)
        self.gpu_combo = QComboBox()
        form.addRow("用于配音的显卡", self.gpu_combo)
        self.hardware_status = note("正在检测显卡……")
        form.addRow(self.hardware_status)
        form.addRow(row(button("重新检测", self.detect_hardware), button("运行完整自检", self.self_test)))
        body.addWidget(hardware)
        components = QGroupBox("模型与运行组件")
        form = QFormLayout(components)
        self.setting_fields = {}
        advanced_settings = QWidget()
        advanced_form = QFormLayout(advanced_settings)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        fields = [("model_dir", "已有模型文件夹（可选）", True), ("component_dir", "模型与组件存放位置", True),
                  ("runtime_python", "推理 Python", False), ("ffmpeg_path", "FFmpeg 程序", False),
                  ("manifest_path", "组件清单（可选）", False)]
        for key, title, directory in fields:
            field = QLineEdit(str(self.settings.get(key, "")))
            self.setting_fields[key] = field
            picker = button("浏览", lambda checked=False, k=key, d=directory: self.browse_setting(k, d))
            destination = form if key in ("model_dir", "component_dir") else advanced_form
            destination.addRow(title, row(field, picker))
        advanced_toggle = QCheckBox("高级设置（已有运行环境 / 自定义组件清单）")
        advanced_settings.setVisible(False)
        advanced_toggle.toggled.connect(advanced_settings.setVisible)
        form.addRow(advanced_toggle)
        form.addRow(advanced_settings)
        self.component_status = note("正在检查组件……")
        form.addRow(self.component_status)
        self.install_all_button = button("自动准备所需组件", self.install_all, True)
        form.addRow(row(button("保存设置", self.apply_settings), self.install_all_button,
                        button("校验已有模型", self.validate_model), button("检查并修复", self.repair_components)))
        form.addRow(note("按检测到的显卡自动匹配环境，通常无需手动设置。模型约 5.43 GB，各类显卡共用一份模型。"))
        body.addWidget(components)
        diagnostics = QGroupBox("运行日志")
        logs = QVBoxLayout(diagnostics)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1500)
        self.logs.setMinimumHeight(170)
        self.logs.setStyleSheet("font-family: Consolas; font-size: 11px;")
        logs.addWidget(self.logs)
        logs.addLayout(row(button("导出诊断信息", self.export_diagnostics), button("打开数据目录", lambda: self.open_path(user_data_dir())), 1))
        body.addWidget(diagnostics)
        body.addWidget(note("兼容范围：RTX 20～50 系列、实际显存 8 GB 及以上。未实测型号会明确标为待验证。PowerPoint 仅用于 PPT 页面渲染。"))
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.gpu_combo.currentIndexChanged.connect(self._gpu_changed)
        return page

    def append_log(self, message):
        if not message:
            return
        entry = datetime.now().strftime("%H:%M:%S ") + str(message)
        if hasattr(self, "logs"):
            self.logs.appendPlainText(entry)
        try:
            with (session_dir() / "desktop.log").open("a", encoding="utf-8") as stream:
                stream.write(entry + "\n")
        except OSError:
            pass

    def show_error(self, message):
        self.append_log(message)
        self.status_label.setText(str(message))
        if self._verification_mode:
            self.verification_finished.emit({"complete": False, "error": str(message)})
        else:
            QMessageBox.warning(self, "需要处理", str(message))

    def _progress(self, event):
        message = str(event.get("message", event.get("stage", "正在处理……")))
        if self._batch_total:
            message = f"第 {self._batch_completed + 1} / {self._batch_total} 个配音任务 · " + message
        self.status_label.setText(message)
        total = event.get("total")
        complete = event.get("completed", 0)
        if total and float(total) > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(1000, max(0, int(float(complete) / float(total) * 1000))))
        else:
            self.progress_bar.setRange(0, 0)
        self.append_log(message)

    def update_busy(self, *args):
        busy = self.worker.busy or self._background is not None or bool(self._batch)
        self.cancel_button.setEnabled(busy)
        for item in (self.text_generate, self.new_project_button, self.open_project_button,
                     self.page_generate, self.batch_button, self.video_button, self.install_all_button):
            item.setEnabled(not busy)
        self.stack.widget(1).setEnabled(not busy)
        self.stack.widget(2).setEnabled(not busy)
        for field in self.setting_fields.values():
            field.setEnabled(not busy)
        self.save_project_button.setEnabled(self.project is not None and not busy)
        self.text_wav.setEnabled(bool(self._text_output) and not busy)
        self.text_mp3.setEnabled(bool(self._text_output) and not busy)
        self.gpu_combo.setEnabled(not busy)
        if not busy:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)

    def background(self, message, function, success=None, failure=None):
        if self._background or self.worker.busy:
            raise RuntimeError("请先等待当前任务完成。")
        self._background_success = success
        self._background_error = failure
        task = BackgroundTask(function, self)
        self._background = task
        task.progress.connect(self._progress)
        task.succeeded.connect(self._background_ok)
        task.failed.connect(self._background_failed)
        task.finished.connect(task.deleteLater)
        self._progress({"message": message})
        self.update_busy()
        task.start()

    def _background_ok(self, result):
        task = self._background
        if task:
            task.wait()
        callback = self._background_success
        self._background = None
        self._background_success = self._background_error = None
        self.update_busy()
        self.status_label.setText("任务完成。")
        try:
            if callback:
                callback(result)
        except Exception as exc:
            self.show_error(str(exc))

    def _background_failed(self, error):
        if self._background:
            self._background.wait()
        callback = self._background_error
        self._background = None
        self._background_success = self._background_error = None
        self._batch_export = None
        self.update_busy()
        if callback:
            callback(error)
        elif "取消" in error or "cancel" in error.lower():
            self.status_label.setText("任务已取消，完整结果已保留。")
        else:
            self.show_error(error)

    def cancel_task(self):
        self._pending_action = None
        self._batch = []
        self._batch_export = None
        if self._background:
            self._background.cancel_event.set()
        if self.worker.busy:
            self.worker.cancel()
        self.status_label.setText("正在停止任务；已完成的结果会保留。")

    def play(self, path):
        if not path or not Path(path).is_file():
            self._set_playback_error("音频文件尚未生成或已移动。")
            return
        self.player.stop()
        self._playback_path = str(Path(path).resolve())
        self._playback_error = ""
        self._playback_stopped_by_user = False
        # A window can remain open while Windows changes its default speaker.
        # QAudioOutput retains its selected device until explicitly updated.
        if not self._sync_audio_device(force=True):
            return
        self.player.setSource(QUrl.fromLocalFile(self._playback_path))
        self.player.play()
        self._update_playback_status()

    def stop_playback(self):
        self._playback_stopped_by_user = True
        self.player.stop()
        self._update_playback_status()

    def _sync_audio_device(self, force=False):
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.player.stop()
            self._set_playback_error("未检测到可用的音频输出设备，请连接音箱或耳机后重试。")
            return False
        if force or self.audio_output.device().id() != device.id():
            self.audio_output.setDevice(device)
            self.append_log("试听输出设备：" + device.description())
        if self._playback_error.startswith("未检测到可用的音频输出设备"):
            self._playback_error = ""
        self._update_playback_status()
        return True

    def _audio_outputs_changed(self):
        # This also redirects an ongoing preview when the default changes.
        self._sync_audio_device()

    def _set_playback_error(self, message):
        self._playback_error = str(message)
        self.append_log("音频播放：" + self._playback_error)
        self._update_playback_status()

    def _playback_failed(self, error, message=""):
        if error != QMediaPlayer.Error.NoError:
            self._set_playback_error(message or self.player.errorString() or "无法播放音频，请检查输出设备后重试。")

    def _update_playback_status(self, *args):
        if not hasattr(self, "playback_label"):
            return
        if self._playback_error:
            self.playback_label.setText("试听失败：" + self._playback_error)
            self.playback_label.setStyleSheet("color: #b42318;")
            return
        self.playback_label.setStyleSheet("color: #395570;")
        device = self.audio_output.device().description() or "系统默认输出"
        if not self._playback_path:
            self.playback_label.setText("试听：尚未播放 · 输出：" + device)
            return
        status = self.player.mediaStatus()
        state = self.player.playbackState()
        if self._playback_stopped_by_user:
            message = "已停止"
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            message = "播放完成"
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            message = "无法读取音频，请重新选择文件"
        elif status == QMediaPlayer.MediaStatus.LoadingMedia:
            message = "正在加载音频"
        elif status == QMediaPlayer.MediaStatus.StalledMedia:
            message = "正在缓冲音频"
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            message = "正在播放"
        elif state == QMediaPlayer.PlaybackState.PausedState:
            message = "已暂停"
        else:
            message = "已停止"
        def timestamp(milliseconds):
            seconds = max(0, int(milliseconds)) // 1000
            return f"{seconds // 60:02d}:{seconds % 60:02d}"
        elapsed = timestamp(self.player.position()) + " / " + timestamp(self.player.duration())
        self.playback_label.setText(f"试听：{message} · {elapsed} · 输出：{device}\n{Path(self._playback_path).name}")

    def open_path(self, path):
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))

    def open_outputs(self):
        target = Path(self._text_output).parent if self._text_output else user_data_dir() / "outputs"
        target.mkdir(parents=True, exist_ok=True)
        self.open_path(target)

    def import_text(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入文字", "", "文本和 Word (*.txt *.docx)")
        if path:
            try:
                self.background("正在读取文档……", lambda p, c: documents.read_text(path), self.text_edit.setPlainText)
            except Exception as exc:
                self.show_error(str(exc))

    def generate_text(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.show_error("请先输入需要配音的文字。")
            return
        try:
            voice = self.text_voice.current()
            if not self._ensure_inference_ready(self.generate_text):
                return
            output = user_data_dir() / "outputs" / (datetime.now().strftime("配音_%Y%m%d_%H%M%S_") + uuid4().hex[:6] + ".wav")
            output.parent.mkdir(parents=True, exist_ok=True)
            self.worker.configure(self.settings)
            self._active_job = {"kind": "text"}
            self.worker.submit("synthesize", text=text, ref_audio=voice["audio"], ref_text=voice["transcript"],
                               speed=self.text_speed.value(), seed=self.text_seed.value(), output_path=str(output))
            self._progress({"message": "正在准备配音引擎；首次加载需要一些时间……"})
        except Exception as exc:
            self._active_job = None
            self.show_error(str(exc))

    def export_text(self, extension):
        if not self._text_output:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出音频", str(Path(self._text_output).with_suffix("." + extension)),
                                              f"{extension.upper()} (*.{extension})")
        if not path:
            return
        path = str(Path(path).with_suffix("." + extension))
        try:
            self.background("正在导出音频……", lambda p, c: media.export_audio(self._text_output, path,
                            ffmpeg=self.ffmpeg(), progress=p, cancel=c),
                            lambda result: self.status_label.setText("音频已导出：" + str(result)))
        except Exception as exc:
            self.show_error(str(exc))

    def _worker_event(self, event):
        kind = event.get("event")
        if kind == "progress":
            self._progress(event)
            return
        job = self._active_job or {}
        self._active_job = None
        if kind == "result":
            try:
                if job.get("kind") == "text":
                    self._text_output = event["path"]
                    self.text_play.setEnabled(True)
                    self.text_result.setText(f"生成完成 · {float(event.get('duration', 0)):.1f} 秒 · {event.get('segments', 1)} 段\n{Path(event['path']).name}")
                    self.status_label.setText("配音已完成，可试听或导出。")
                    self._save_text_state()
                elif job.get("kind") == "page":
                    slide = self.project["slides"][job["index"]]
                    projects.remember_audio(self.project, slide, self.project_dir, event["path"], job["fingerprint"])
                    self._batch_completed += 1
                    projects.save_project(self.project, self.project_dir)
                    self._refresh_slides(keep=True)
                    QTimer.singleShot(0, self._next_page)
                elif job.get("kind") == "self_test":
                    atomic_json(user_data_dir() / "last-self-test.json", {
                        "timestamp": datetime.now().isoformat(), "gpu_uuid": self.settings.get("gpu_uuid"),
                        "runtime_id": self.settings.get("runtime_id"), "result": event,
                        "signature": self._self_test_signature, "inference_success": True, "complete": False,
                    })
                    if Path(self.settings.get("ffmpeg_path", "")).is_file():
                        self._check_video_encoder(event)
                    else:
                        self.status_label.setText("CUDA 与语音合成自检通过。视频/MP3 功能仍需准备 FFmpeg。")
                        if self._verification_mode:
                            self.verification_finished.emit({"complete": False, "inference_success": True,
                                                             "error": "缺少 FFmpeg，视频编码自检尚未完成。", "inference": event})
                        self._resume_pending_action()
            except Exception as exc:
                self._batch = []
                self._batch_export = None
                self.show_error(str(exc))
        elif kind in ("error", "cancelled"):
            self._pending_action = None
            self._batch = []
            self._batch_export = None
            if job.get("kind") == "page" and self.project:
                self.project["slides"][job["index"]]["status"] = "pending" if kind == "cancelled" else "error"
                projects.save_project(self.project, self.project_dir)
                self._refresh_slides(keep=True)
            if kind == "error":
                self.show_error(event.get("message", "生成失败，请查看运行日志。"))
            else:
                self.status_label.setText(event.get("message", "任务已取消。"))
        self.update_busy()

    def voice_by_id(self, voice_id):
        for voice in self.voice_items:
            if voice["id"] == voice_id:
                return voice
        if self.project and voice_id in self.project.get("voices", {}):
            voice = dict(self.project["voices"][voice_id])
            voice["audio"] = projects.resolve_resource(self.project_dir, voice["audio"])
            return voice
        return None

    def project_voice_by_id(self, voice_id):
        if self.project and voice_id in self.project.get("voices", {}):
            voice = dict(self.project["voices"][voice_id])
            voice["audio"] = projects.resolve_resource(self.project_dir, voice["audio"])
            return voice
        return self.voice_by_id(voice_id)

    def _refresh_voice_list(self):
        self.voice_items = self.library.list()
        selected = self.voice_list.currentRow()
        self.voice_list.clear()
        for voice in self.voice_items:
            tag = "自建" if voice.get("kind") == "custom" else "预设"
            self.voice_list.addItem(f"{voice['name']}\n{tag} · {voice.get('license', '')}")
        self.voice_list.setCurrentRow(max(0, min(selected, len(self.voice_items) - 1)))
        self.text_voice.refresh()
        self.ppt_voice.refresh()
        self._refresh_page_voices()

    def show_voice(self, index):
        if index < 0 or index >= len(self.voice_items):
            return
        voice = self.voice_items[index]
        self.voice_name.setText(voice["name"])
        self.voice_source.setText(f"{voice.get('source', '')}\n{voice.get('license', '')}")
        self.voice_text.setPlainText(voice["transcript"])

    def preview_library_voice(self, demo=False):
        index = self.voice_list.currentRow()
        if index < 0:
            return
        voice = self.voice_items[index]
        audio = voice.get("demo_audio") if demo else voice["audio"]
        if not audio:
            self.status_label.setText("这个音色还没有合成示例，可先用于文本配音生成试听。")
            return
        self.play(audio)

    def use_library_voice(self):
        index = self.voice_list.currentRow()
        if index >= 0:
            self.text_voice.select(self.voice_items[index]["id"])
            self.navigation.setCurrentRow(0)

    def add_voice(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("导入自建音色")
        dialog.resize(610, 390)
        layout = QVBoxLayout(dialog)
        name = QLineEdit()
        name.setPlaceholderText("给这个声音起一个名称")
        audio = QLineEdit()
        def choose():
            path, _ = QFileDialog.getOpenFileName(dialog, "选择参考录音", "", "音频 (*.wav *.flac *.mp3 *.ogg)")
            if path:
                audio.setText(path)
        text = QPlainTextEdit()
        text.setPlaceholderText("填写录音中实际说出的完整原文")
        layout.addWidget(QLabel("音色名称"))
        layout.addWidget(name)
        layout.addLayout(row(audio, button("选择录音", choose)))
        layout.addWidget(note("建议使用 3～15 秒、只有一个人说话、没有背景音乐的清晰录音。"))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存音色")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        def save():
            try:
                self.library.add(name.text().strip(), audio.text().strip(), text.toPlainText().strip())
                dialog.accept()
                self._refresh_voice_list()
            except Exception as exc:
                QMessageBox.warning(dialog, "无法保存音色", str(exc))
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def rename_voice(self):
        index = self.voice_list.currentRow()
        if index < 0:
            return
        voice = self.voice_items[index]
        if voice.get("kind") != "custom":
            self.show_error("预设音色保留原始名称；自建音色可以改名。")
            return
        value, ok = QInputDialog.getText(self, "音色改名", "新名称", text=voice["name"])
        if ok:
            try:
                self.library.rename(voice["id"], value.strip())
                self._refresh_voice_list()
            except Exception as exc:
                self.show_error(str(exc))

    def delete_voice(self):
        index = self.voice_list.currentRow()
        if index < 0:
            return
        voice = self.voice_items[index]
        if voice.get("kind") != "custom":
            self.show_error("预设音色不能在这里删除。")
            return
        if QMessageBox.question(self, "删除自建音色", f"删除“{voice['name']}”？已保存工程里的录音副本会保留。") == QMessageBox.StandardButton.Yes:
            try:
                self.library.delete(voice["id"])
                self._refresh_voice_list()
            except Exception as exc:
                self.show_error(str(exc))

    def _confirm_replace_project(self):
        if self.worker.busy or self._background:
            self.show_error("请先等待当前任务完成或取消。")
            return False
        if self._dirty and self.project:
            choice = QMessageBox.question(self, "保存当前工程", "当前工程有修改，是否先保存？",
                                           QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if choice == QMessageBox.StandardButton.Cancel:
                return False
            if choice == QMessageBox.StandardButton.Save:
                return self.save_project()
        return True

    def new_project(self):
        if not self._confirm_replace_project():
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入 PowerPoint", "", "PowerPoint (*.pptx)")
        if not path:
            return
        parent = QFileDialog.getExistingDirectory(self, "选择保存工程的文件夹（将新建独立工程子目录）")
        if not parent:
            return
        folder = Path(parent) / (Path(path).stem + "_配音工程_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            voice = self.ppt_voice.current()
            speed = self.ppt_speed.value()
            def build(progress, cancel):
                progress({"message": "正在读取 PPT 备注并创建工程……"})
                project = projects.create_project(path, folder, voice=voice, speed=speed)
                if cancel():
                    raise RuntimeError("任务已取消。")
                render_error = ""
                if documents.powerpoint_available():
                    try:
                        images = documents.render_pptx(path, folder / "images", progress=progress, cancel=cancel)
                        for slide in project["slides"]:
                            image = images.get(slide["number"])
                            if image:
                                slide["image"] = str(Path(image).relative_to(folder)).replace("\\", "/")
                    except Exception as exc:
                        render_error = str(exc)
                else:
                    render_error = "未检测到 Microsoft PowerPoint。已导入备注，仍可逐页配音；安装 PowerPoint 后可再次尝试导出视频。"
                projects.save_project(project, folder)
                return project, folder, render_error
            self.background("正在导入 PPT……", build, self._project_created)
        except Exception as exc:
            self.show_error(str(exc))

    def _project_created(self, result):
        project, directory, warning = result
        self._set_project(project, directory)
        if warning:
            self.show_error(warning)
        else:
            self.status_label.setText(f"已导入 {len(project['slides'])} 页，可编辑讲稿并生成配音。")

    def open_project(self):
        if not self._confirm_replace_project():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "打开配音工程", "", "配音工程 (project.json)")
        if filename:
            try:
                self._set_project(projects.load_project(Path(filename).parent), Path(filename).parent)
            except Exception as exc:
                self.show_error(str(exc))

    def _set_project(self, project, directory):
        self._updating = True
        self.project, self.project_dir = project, Path(directory)
        self.current_slide = None
        settings = project.get("settings", {})
        self._refresh_voice_list()
        self._include_project_voices()
        self.ppt_voice.select(settings.get("voice_id", ""))
        self.ppt_speed.setValue(settings.get("speed", 1.0))
        self.ppt_tail.setValue(settings.get("tail_silence", .5))
        self._updating = False
        self._dirty = False
        self.project_title.setText(f"{self.project_dir.name} · {len(project['slides'])} 页")
        self.project_title.setToolTip(str(self.project_dir))
        self._refresh_slides()
        self.slide_list.setCurrentRow(0)
        self.update_busy()

    def _include_project_voices(self):
        if not self.project:
            return
        for voice_id, voice in self.project.get("voices", {}).items():
            if self.ppt_voice.combo.findData(voice_id) < 0:
                self.ppt_voice.combo.insertItem(max(0, self.ppt_voice.combo.count() - 1), voice.get("name", "工程音色") + "（工程）", voice_id)
        self._refresh_page_voices()

    def _refresh_page_voices(self):
        if not hasattr(self, "page_voice"):
            return
        current = self.page_voice.currentData()
        self.page_voice.blockSignals(True)
        self.page_voice.clear()
        self.page_voice.addItem("跟随全局音色", "")
        found = set()
        for voice in self.voice_items:
            found.add(voice["id"])
            self.page_voice.addItem(voice["name"], voice["id"])
        if self.project:
            for voice_id, voice in self.project.get("voices", {}).items():
                if voice_id not in found:
                    self.page_voice.addItem(voice.get("name", "工程音色") + "（工程）", voice_id)
        self.page_voice.setCurrentIndex(max(0, self.page_voice.findData(current)))
        self.page_voice.blockSignals(False)

    def _refresh_slides(self, keep=False):
        selected = self.current_slide if keep else 0
        self._updating = True
        self.slide_list.blockSignals(True)
        self.slide_list.clear()
        if self.project:
            names = {"pending": "待生成", "complete": "已生成", "completed": "已生成", "ready": "已生成",
                     "error": "生成失败", "stale": "需要重生成", "generating": "生成中"}
            for slide in self.project["slides"]:
                state = "静音页面" if not slide["text"].strip() else names.get(slide.get("status", "pending"), slide.get("status", "待生成"))
                hidden = " · 隐藏页" if slide.get("hidden") else ""
                item = QListWidgetItem(f"第 {slide['number']} 页{hidden}\n{slide.get('title', '')[:10]}\n{state}")
                item.setToolTip(slide.get("title", ""))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if slide.get("included", True) else Qt.CheckState.Unchecked)
                if slide.get("image"):
                    picture = QPixmap(projects.resolve_resource(self.project_dir, slide["image"]))
                    if not picture.isNull():
                        item.setIcon(QIcon(picture))
                self.slide_list.addItem(item)
        self._updating = False
        self.slide_list.blockSignals(False)
        if self.project and self.project["slides"]:
            self.current_slide = None
            index = max(0, min(selected or 0, len(self.project["slides"]) - 1))
            self.slide_list.setCurrentRow(index)
            if self.current_slide is None:
                self.select_slide(index)

    def select_slide(self, index):
        if self._updating or not self.project or index < 0:
            return
        self._commit_page()
        self.current_slide = index
        slide = self.project["slides"][index]
        self._updating = True
        self.page_text.setPlainText(slide.get("text", ""))
        self.page_label.setText(f"第 {slide['number']} 页讲稿")
        self.page_voice.setCurrentIndex(max(0, self.page_voice.findData(slide.get("voice_id", ""))))
        self.page_speed_follow.setChecked(slide.get("speed") is None)
        self.page_speed.setValue(slide.get("speed") or self.ppt_speed.value())
        self.page_speed.setEnabled(slide.get("speed") is not None)
        self.page_blank.setValue(slide.get("blank_seconds", 3))
        self.slide_preview.setPixmap(QPixmap())
        self.slide_preview.setText("尚无页面预览")
        if slide.get("image"):
            pixmap = QPixmap(projects.resolve_resource(self.project_dir, slide["image"]))
            if not pixmap.isNull():
                self.slide_preview.setPixmap(pixmap.scaled(300, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self._updating = False

    def _commit_page(self):
        if self._updating or not self.project or self.current_slide is None:
            return
        slide = self.project["slides"][self.current_slide]
        changes = {"text": self.page_text.toPlainText(), "voice_id": self.page_voice.currentData() or "",
                   "speed": None if self.page_speed_follow.isChecked() else self.page_speed.value(),
                   "blank_seconds": self.page_blank.value()}
        if any(slide.get(key) != value for key, value in changes.items()):
            slide.update(changes)
            if slide.get("audio"):
                slide["status"] = "stale"
            self._dirty = True

    def _page_changed(self, *args):
        self._commit_page()

    def _slide_checked(self, item):
        if self._updating or not self.project:
            return
        index = self.slide_list.row(item)
        self.project["slides"][index]["included"] = item.checkState() == Qt.CheckState.Checked
        self._dirty = True

    def _global_changed(self, *args):
        if self._updating or not self.project:
            return
        self._dirty = True
        for slide in self.project["slides"]:
            if slide.get("audio"):
                slide["status"] = "stale"

    def _snapshot_project(self):
        self._commit_page()
        voice = self.ppt_voice.current()
        self.project.setdefault("settings", {}).update(voice_id=voice["id"], speed=self.ppt_speed.value(),
                                                        seed=0, tail_silence=self.ppt_tail.value())
        selected = {voice["id"]: voice}
        for slide in self.project["slides"]:
            voice_id = slide.get("voice_id")
            if voice_id:
                item = self.project_voice_by_id(voice_id)
                if not item:
                    raise ValueError(f"第 {slide['number']} 页使用的音色不可用，请重新选择。")
                selected[voice_id] = item
        self.project["model_id"] = "Fun-CosyVoice3-0.5B-2512"
        self.project["runtime_id"] = self.settings.get("runtime_id", "")
        projects.save_project(self.project, self.project_dir, voices=selected)
        self._dirty = False

    def save_project(self):
        if not self.project:
            return False
        try:
            self._snapshot_project()
            self.status_label.setText("工程已保存。")
            return True
        except Exception as exc:
            self.show_error(str(exc))
            return False

    def open_project_folder(self):
        self.open_path(self.project_dir)

    def save_script_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存讲稿模板", "PPT讲稿模板.txt", "文本 (*.txt)")
        if path:
            try:
                Path(path).write_text("第1页\n这里填写第一页的完整讲稿。\n\n第2页\n这里填写第二页的完整讲稿。\n\n第3页\n这里填写第三页的完整讲稿。\n", encoding="utf-8-sig")
                self.status_label.setText("模板已保存。标题独立成段，页码对应 PPT 的实际页码。")
            except OSError as exc:
                self.show_error(str(exc))

    def import_script(self):
        if not self.project:
            self.show_error("请先导入 PPT 或打开工程。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入逐页讲稿", "", "讲稿 (*.txt *.docx)")
        if not path:
            return
        try:
            self.background("正在读取并匹配讲稿页码……", lambda p, c: documents.parse_page_script(
                documents.read_text(path), len(self.project["slides"])), self._preview_script)
        except Exception as exc:
            self.show_error(str(exc))

    def _preview_script(self, mapping):
        dialog = QDialog(self)
        dialog.setWindowTitle("预览讲稿对应关系")
        dialog.resize(830, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(note("应用后只覆盖下列匹配页面，其他页面保留原讲稿。"))
        table = QTableWidget(len(mapping), 3)
        table.setHorizontalHeaderLabels(["PPT 页码", "原讲稿", "导入讲稿"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for index, (number, text) in enumerate(sorted(mapping.items())):
            table.setItem(index, 0, QTableWidgetItem(str(number)))
            table.setItem(index, 1, QTableWidgetItem(self.project["slides"][number - 1]["text"]))
            table.setItem(index, 2, QTableWidgetItem(text))
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("应用匹配讲稿")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._commit_page()
            for number, text in mapping.items():
                slide = self.project["slides"][number - 1]
                slide["text"] = text
                slide["status"] = "stale" if slide.get("audio") else "pending"
            self._dirty = True
            self._refresh_slides(keep=True)
            self.status_label.setText(f"已替换 {len(mapping)} 页讲稿。")

    def play_page(self):
        if self.project and self.current_slide is not None:
            slide = self.project["slides"][self.current_slide]
            self.play(projects.resolve_resource(self.project_dir, slide["audio"]) if slide.get("audio") else None)

    def generate_page(self):
        if self.project and self.current_slide is not None:
            self._start_batch([self.current_slide], force=True)

    def generate_all(self):
        if not self.project:
            self.show_error("请先导入 PPT 或打开工程。")
            return
        self._start_batch([i for i, slide in enumerate(self.project["slides"]) if slide.get("included", True)])

    def generate_video(self):
        if not self.project:
            self.show_error("请先导入 PPT 或打开工程。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出讲解视频", str(self.project_dir / "讲解视频.mp4"), "MP4 视频 (*.mp4)")
        if path:
            self._start_batch([i for i, slide in enumerate(self.project["slides"]) if slide.get("included", True)],
                              export=str(Path(path).with_suffix(".mp4")))

    def _start_batch(self, indices, force=False, export=None):
        if self.worker.busy or self._background:
            return
        try:
            self._commit_page()
            if not indices:
                raise ValueError("请至少勾选一页用于生成或导出。")
            self._snapshot_project()
            self._batch = []
            self._batch_export = export
            for index in indices:
                slide = self.project["slides"][index]
                if not slide["text"].strip():
                    continue
                effective = projects.effective_settings(self.project, slide)
                voice = self.project_voice_by_id(effective["voice_id"])
                if not voice:
                    raise ValueError(f"第 {slide['number']} 页的参考音色不可用。")
                fingerprint = projects.fingerprint(slide["text"], voice, effective["speed"], effective["seed"],
                                                   self.project["model_id"], self.project["runtime_id"])
                cached = None if force else projects.cached_audio(slide, self.project_dir, fingerprint)
                if cached:
                    slide["status"] = "complete"
                    continue
                self._batch.append({"index": index, "voice": voice, "settings": effective, "fingerprint": fingerprint})
            if self._batch and not self._ensure_inference_ready(lambda: self._start_batch(indices, force, export)):
                self._batch = []
                self._batch_export = None
                return
            self.worker.configure(self.settings)
            self._batch_total = len(self._batch)
            self._batch_completed = 0
            self._next_page()
        except Exception as exc:
            self._batch = []
            self._batch_export = None
            self.update_busy()
            self.show_error(str(exc))

    def _next_page(self):
        if self.worker.busy:
            return
        if not self._batch:
            self._batch_total = 0
            projects.save_project(self.project, self.project_dir)
            self._refresh_slides(keep=True)
            self.status_label.setText("逐页配音已完成；已复用内容一致的现有音频。")
            target, self._batch_export = self._batch_export, None
            if target:
                self._export_video(target)
            self.update_busy()
            return
        job = self._batch.pop(0)
        slide = self.project["slides"][job["index"]]
        try:
            target = session_dir() / (f"page_{slide['number']:04d}_{uuid4().hex}.wav")
            self._active_job = dict(job, kind="page")
            self.worker.submit("synthesize", text=slide["text"], ref_audio=job["voice"]["audio"],
                               ref_text=job["voice"]["transcript"], speed=job["settings"]["speed"],
                               seed=job["settings"]["seed"], output_path=str(target))
            slide["status"] = "generating"
            self._progress({"message": f"正在生成第 {slide['number']} 页……"})
            self._refresh_slides(keep=True)
        except Exception as exc:
            self._active_job = None
            self._batch = []
            self._batch_export = None
            self.show_error(str(exc))
        self.update_busy()

    def _export_video(self, destination):
        selected = [slide for slide in self.project["slides"] if slide.get("included", True)]
        if not selected:
            self.show_error("请至少勾选一页用于导出。")
            return
        tail_silence = self.ppt_tail.value()
        def export(progress, cancel):
            missing = [s for s in selected if not s.get("image") or not Path(projects.resolve_resource(self.project_dir, s["image"])).is_file()]
            if missing:
                if not documents.powerpoint_available():
                    raise ValueError("未检测到 Microsoft PowerPoint，无法渲染页面。请先安装 PowerPoint，再重试导出视频。")
                source = self.project.get("source", "")
                if isinstance(source, dict):
                    source = source.get("path", "")
                source = projects.resolve_resource(self.project_dir, source)
                images = documents.render_pptx(source, self.project_dir / "images", progress=progress, cancel=cancel)
                for slide in self.project["slides"]:
                    if slide["number"] in images:
                        slide["image"] = str(Path(images[slide["number"]]).relative_to(self.project_dir)).replace("\\", "/")
                projects.save_project(self.project, self.project_dir)
            gpu = next((x for x in self._gpu_items if x["uuid"] == self.settings.get("gpu_uuid")), {})
            return media.export_video(selected, self.project_dir, destination, ffmpeg=self.ffmpeg(),
                                      gpu_index=int(gpu.get("index", 0)), tail_silence=tail_silence,
                                      progress=progress, cancel=cancel)
        try:
            self.background("正在合成讲解视频……", export, self._video_exported)
        except Exception as exc:
            self.show_error(str(exc))

    def _video_exported(self, result):
        self.status_label.setText(f"视频已导出 · {float(result.get('duration', 0)):.1f} 秒\n{result['path']}")
        self.append_log(json.dumps(result, ensure_ascii=False))
        self._refresh_slides(keep=True)

    def ffmpeg(self):
        return self.settings.get("ffmpeg_path") or "ffmpeg"

    def _environment_signature(self):
        """Bind a real synthesis check to the hardware and installed components."""
        from . import __version__
        gpu = self.gpu_combo.currentData() or {}
        info = {"app_version": __version__, "gpu": {key: gpu.get(key) for key in
                ("uuid", "driver", "compute_capability", "total_mb")},
                "runtime_id": self.settings.get("runtime_id"), "files": []}
        runtime = Path(self.settings.get("runtime_python", ""))
        model = Path(self.settings.get("model_dir", ""))
        manifest = Path(self.settings.get("manifest_path") or app_root() / "packaging" / "component-manifest.json")
        for source in (runtime, runtime.parent / ".complete.json", model / "cosyvoice3.yaml", manifest):
            if source.is_file():
                stat = source.stat()
                info["files"].append({"path": str(source.resolve()), "size": stat.st_size,
                                      "mtime_ns": stat.st_mtime_ns,
                                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest() if stat.st_size < 10_000_000 else ""})
            else:
                info["files"].append({"path": str(source), "missing": True})
        return hashlib.sha256(json.dumps(info, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def _ensure_inference_ready(self, continuation):
        gpu = self.gpu_combo.currentData()
        if self._background or self.worker.busy:
            return False
        if time.monotonic() - self._last_gpu_probe > 5:
            self._pending_action = continuation
            self.detect_hardware()
            return False
        if not gpu:
            self._pending_action = continuation
            self.navigation.setCurrentRow(3)
            self.show_error("请先在设置中检测显卡并准备运行组件。讲稿已保留。")
            return False
        own_model_loaded = self.worker.process.state() != QProcess.ProcessState.NotRunning
        if gpu.get("status") != "compatible" and not (gpu.get("status") == "busy" and own_model_loaded):
            self._pending_action = continuation if gpu.get("status") == "busy" else None
            self.navigation.setCurrentRow(3)
            self.show_error(gpu.get("message", "当前显卡不符合运行要求。") + "\n处理完成后点击“重新检测”，可继续保留的任务。")
            return False
        runtime = Path(self.settings.get("runtime_python", ""))
        model = Path(self.settings.get("model_dir", ""))
        if not runtime.is_file() or not (model / "cosyvoice3.yaml").is_file():
            self._pending_action = continuation
            self.navigation.setCurrentRow(3)
            self.show_error("请先自动准备运行组件和模型，或选择已有安装。完成自检后将继续当前任务。")
            return False
        signature = self._environment_signature()
        report_path = user_data_dir() / "last-self-test.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        if report.get("signature") == signature and report.get("inference_success"):
            return True
        self._pending_action = continuation
        self.status_label.setText("检测到新的显卡或运行环境，先执行实际合成自检，再继续当前任务。")
        self.self_test()
        return False

    def _resume_pending_action(self):
        callback, self._pending_action = self._pending_action, None
        if callback:
            QTimer.singleShot(0, callback)

    def _save_text_state(self):
        try:
            directory = user_data_dir() / "text-session"
            state = {"text": self.text_edit.toPlainText(), "voice_id": self.text_voice.combo.currentData(),
                     "speed": self.text_speed.value(), "seed": self.text_seed.value(),
                     "output": self._text_output or "", "result_text": self.text_result.text()}
            if state["voice_id"] == "__temporary__":
                original = Path(self.text_voice.audio.text().strip())
                state["transcript"] = self.text_voice.transcript.toPlainText()
                if original.is_file():
                    directory.mkdir(parents=True, exist_ok=True)
                    reference = directory / ("reference" + original.suffix.lower())
                    if original.resolve() != reference.resolve():
                        shutil.copyfile(original, reference)
                    state["audio"] = str(reference)
            atomic_json(directory / "draft.json", state)
        except Exception as exc:
            self.append_log("文本草稿保存失败：" + str(exc))

    def _restore_text_state(self):
        try:
            state = json.loads((user_data_dir() / "text-session" / "draft.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.text_edit.setPlainText(str(state.get("text", "")))
        self.text_voice.select(state.get("voice_id", ""))
        self.text_speed.setValue(float(state.get("speed", 1)))
        self.text_seed.setValue(int(state.get("seed", 0)))
        if state.get("voice_id") == "__temporary__":
            self.text_voice.audio.setText(state.get("audio", ""))
            self.text_voice.transcript.setPlainText(state.get("transcript", ""))
        output = state.get("output")
        if output and Path(output).is_file():
            self._text_output = output
            self.text_result.setText(state.get("result_text", "已恢复上一次生成结果。"))
            for item in (self.text_play, self.text_wav, self.text_mp3):
                item.setEnabled(True)

    def browse_setting(self, key, directory):
        if directory:
            path = QFileDialog.getExistingDirectory(self, "选择文件夹", self.setting_fields[key].text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.setting_fields[key].text(),
                                                "组件清单 (*.json)" if key == "manifest_path" else "程序 (*.exe);;所有文件 (*)")
        if path:
            self.setting_fields[key].setText(path)

    def apply_settings(self, quiet=False):
        try:
            if self.worker.busy or self._background:
                raise ValueError("请先等待当前任务完成，再修改运行设置。")
            from .paths import outside_sync
            updated = dict(self.settings)
            for key, field in self.setting_fields.items():
                updated[key] = field.text().strip()
            if not updated.get("component_dir"):
                raise ValueError("请选择组件存放位置。")
            outside_sync(updated["component_dir"])
            gpu = self.gpu_combo.currentData()
            if gpu:
                updated["gpu_uuid"] = gpu["uuid"]
                updated["runtime_id"] = gpu.get("runtime_id") or updated.get("runtime_id", "cu121")
            self.worker.configure(updated)
            self.settings = updated
            save_settings(updated)
            self._refresh_components()
            if not quiet:
                self.status_label.setText("设置已保存。运行环境或显卡变化后，请执行完整自检。")
            return True
        except Exception as exc:
            self.show_error(str(exc))
            return False

    def _refresh_components(self):
        from .runtime import resolve_components
        try:
            resolved = resolve_components(self.settings)
            for key in ("runtime_python", "model_dir", "ffmpeg_path"):
                if resolved.get(key) and not self.settings.get(key):
                    self.settings[key] = resolved[key]
                    self.setting_fields[key].setText(resolved[key])
            names = {"runtime-cu121": "RTX 20～40 运行组件", "runtime-cu128": "RTX 50 运行组件",
                     "model-cosyvoice3": "CosyVoice 3 模型", "ffmpeg": "视频/MP3 编码组件"}
            missing = resolved.get("missing", [])
            self.component_status.setText("组件已就绪，可执行完整自检。" if not missing else "需要准备：" + "、".join(names.get(x, x) for x in missing))
            self.worker.configure(self.settings)
            save_settings(self.settings)
            return resolved
        except Exception as exc:
            self.component_status.setText("组件检查失败：" + str(exc))
            self.append_log(str(exc))
            return {"ready": False, "missing": []}

    def detect_hardware(self):
        if self._background or self.worker.busy:
            self.status_label.setText("请等待当前任务完成后重新检测显卡。")
            return
        try:
            from .gpu import detect_gpus
            self.background("正在检查显卡与运行组件……", lambda p, c: detect_gpus(), self._hardware_detected,
                            self._hardware_detection_failed)
        except Exception as exc:
            self.show_error(str(exc))

    def _hardware_detected(self, gpus):
        from .gpu import select_gpu
        self._last_gpu_probe = time.monotonic()
        self._gpu_items = gpus
        self.gpu_combo.blockSignals(True)
        self.gpu_combo.clear()
        for gpu in gpus:
            self.gpu_combo.addItem(f"{gpu['name']} · {gpu['total_mb'] / 1024:.1f} GB", gpu)
        selected = select_gpu(gpus, self.settings.get("gpu_uuid", ""))
        if not selected:
            selected = next((g for g in gpus if g["uuid"] == self.settings.get("gpu_uuid")), None)
        if selected:
            self.gpu_combo.setCurrentIndex(next(i for i, g in enumerate(gpus) if g["uuid"] == selected["uuid"]))
        self.gpu_combo.blockSignals(False)
        self._gpu_changed()
        components = self._refresh_components()
        if not gpus:
            self.hardware_status.setText("未检测到 NVIDIA 显卡。请检查显卡及驱动；首版需要 RTX 20～50 系列和至少 8 GB 显存。")
        self.status_label.setText("环境检测完成。首次使用可在设置中准备组件并运行完整自检。")
        if components.get("missing") and not self._verification_mode:
            self.navigation.setCurrentRow(3)
            self.status_label.setText("首次使用请确认模型与组件存放位置，然后点击“自动准备所需组件”。")
        if self._pending_action:
            self._resume_pending_action()
        self.hardware_ready.emit()

    def _hardware_detection_failed(self, error):
        self.hardware_status.setText("检测失败：" + error)
        self.show_error(error)

    def _gpu_changed(self, *args):
        gpu = self.gpu_combo.currentData()
        if not gpu:
            return
        previous_runtime = self.settings.get("runtime_id")
        runtime_id = gpu.get("runtime_id")
        if previous_runtime and runtime_id and previous_runtime != runtime_id:
            self.setting_fields["runtime_python"].clear()
            self.settings["runtime_python"] = ""
        self.settings["gpu_uuid"] = gpu["uuid"]
        if runtime_id:
            self.settings["runtime_id"] = runtime_id
        tested = "目标兼容，桌面版需本机自检"
        self.hardware_status.setText(f"{gpu.get('message', '')}\n驱动 {gpu.get('driver', '未知')} · 计算能力 {gpu.get('compute_capability', '未知')} · 可用显存 {gpu.get('free_mb', 0) / 1024:.1f} GB\n{tested}")
        if not self.worker.busy:
            self.worker.configure(self.settings)
            save_settings(self.settings)

    def validate_model(self):
        if not self.apply_settings(quiet=True):
            return
        path = self.settings.get("model_dir")
        if not path:
            self.show_error("请先选择已有模型目录。")
            return
        try:
            from .runtime import validate_model
            def finished(result):
                if result.get("valid"):
                    self.status_label.setText(f"模型校验通过 · {result.get('checked', 0)} 个文件。")
                else:
                    self.show_error("模型校验未通过：\n" + "\n".join(str(x) for x in result.get("errors", [])))
            self.background("正在逐个校验模型文件……", lambda p, c: validate_model(path, progress=p, cancel=c), finished)
        except Exception as exc:
            self.show_error(str(exc))

    def install_all(self):
        if not self.apply_settings(quiet=True):
            return
        gpu = self.gpu_combo.currentData()
        if not gpu or not gpu.get("runtime_id") or gpu.get("status") in ("unsupported_architecture", "insufficient_vram", "driver_update_required"):
            self.show_error("请先选择符合要求的 RTX 显卡并更新驱动，再准备运行组件。")
            return
        try:
            from .runtime import resolve_components
            resolved = resolve_components(self.settings)
            self._install_queue = list(resolved.get("missing", []))
            self._install_next()
        except Exception as exc:
            self.show_error(str(exc))

    def repair_components(self):
        if not self.apply_settings(quiet=True):
            return
        gpu = self.gpu_combo.currentData()
        if not gpu or not gpu.get("runtime_id") or gpu.get("status") in ("unsupported_architecture", "insufficient_vram", "driver_update_required"):
            self.show_error("请先选择符合要求的 RTX 显卡并更新驱动，再检查运行组件。")
            return
        # Repair canonical managed packages, leaving user-selected external environments intact.
        self.worker.stop()
        self._install_queue = ["runtime-" + gpu["runtime_id"], "model-cosyvoice3", "ffmpeg"]
        self.status_label.setText("将校验并修复组件存放位置中的官方组件，已有自定义环境会保留。")
        self._install_next()

    def _install_next(self):
        if not self._install_queue:
            self._refresh_components()
            self.self_test()
            return
        identifier = self._install_queue.pop(0)
        try:
            from .runtime import install_component
            def installed(result):
                for key in ("runtime_python", "runtime_id", "model_dir", "ffmpeg_path"):
                    if result.get(key):
                        self.settings[key] = result[key]
                        if key in self.setting_fields:
                            self.setting_fields[key].setText(result[key])
                save_settings(self.settings)
                self._install_next()
            self.background("正在下载并准备 " + identifier + "……",
                            lambda p, c: install_component(identifier, settings=dict(self.settings), progress=p, cancel=c), installed)
        except Exception as exc:
            self.show_error(str(exc))

    def self_test(self):
        if not self.apply_settings(quiet=True):
            return
        try:
            gpu = self.gpu_combo.currentData()
            own_model_loaded = self.worker.process.state() != QProcess.ProcessState.NotRunning
            if not gpu or (gpu.get("status") != "compatible" and not (gpu.get("status") == "busy" and own_model_loaded)):
                raise ValueError((gpu or {}).get("message", "未检测到符合要求的 NVIDIA 显卡，请先完成显卡检测。"))
            self._self_test_signature = self._environment_signature()
            voice = self.voice_items[0]
            self._active_job = {"kind": "self_test"}
            self.worker.submit("self_test", text="你好，欢迎使用本地配音工作台。现在进行显卡与语音生成检查。",
                               ref_audio=voice["audio"], ref_text=voice["transcript"], speed=1.0, seed=0,
                               output_path=str(session_dir() / "self-test.wav"))
            self._progress({"message": "正在执行 CUDA 运算与实际短句合成自检……"})
        except Exception as exc:
            self._active_job = None
            self.show_error(str(exc))

    def _check_video_encoder(self, inference_result):
        from PIL import Image
        folder = session_dir() / ("encoder-check-" + uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=True)
        image = folder / "slide.png"
        Image.new("RGB", (1920, 1080), (24, 38, 58)).save(image)
        shutil.copyfile(inference_result["path"], folder / "speech.wav")
        slides = [{"number": 1, "included": True, "text": "自检", "image": "slide.png", "audio": "speech.wav", "blank_seconds": 3}]
        gpu = self.gpu_combo.currentData() or {}
        def finished(result):
            report = {"timestamp": datetime.now().isoformat(), "gpu": gpu, "runtime_id": self.settings.get("runtime_id"),
                      "inference": inference_result, "video": result, "complete": True,
                      "signature": self._self_test_signature, "inference_success": True}
            atomic_json(user_data_dir() / "last-self-test.json", report)
            self.status_label.setText("完整自检通过：CUDA 运算、模型合成、视频编码均成功。")
            self.hardware_status.setText(self.hardware_status.text() + "\n本机完整自检已通过。")
            self.append_log("完整自检通过：" + str(result.get("encoder", "")))
            self.verification_finished.emit(report)
            self._resume_pending_action()
        def failed(error):
            atomic_json(user_data_dir() / "last-self-test.json", {"inference": inference_result, "video_error": error, "complete": False,
                                                                 "signature": self._self_test_signature, "inference_success": True})
            self.show_error("语音生成自检通过，视频编码自检未通过：\n" + error)
            self._resume_pending_action()
        self.background("语音检查通过，正在验证视频编码……",
                        lambda p, c: media.export_video(slides, folder, folder / "check.mp4", ffmpeg=self.ffmpeg(),
                                                        gpu_index=int(gpu.get("index", 0)), tail_silence=0,
                                                        progress=p, cancel=c), finished, failed)

    def export_diagnostics(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出诊断信息", "CosyVoice诊断.json", "JSON (*.json)")
        if path:
            try:
                report = {"application": "CosyVoice-Desktop", "timestamp": datetime.now().isoformat(),
                          "gpus": self._gpu_items, "settings": self.settings, "logs": self.logs.toPlainText()}
                previous = user_data_dir() / "last-self-test.json"
                if previous.exists():
                    report["last_self_test"] = json.loads(previous.read_text(encoding="utf-8"))
                # This is a user-requested final artifact, written directly to its destination.
                Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.status_label.setText("诊断信息已导出，可自行查看后分享。")
            except Exception as exc:
                self.show_error(str(exc))

    def closeEvent(self, event):
        self._placement_timer.stop()
        self._startup_timer.stop()
        if self.worker.busy or self._background:
            answer = QMessageBox.question(self, "任务仍在进行", "是否取消当前任务并关闭？完整结果会保留。")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._batch = []
            self._batch_export = None
            if self._background:
                self._background.cancel_event.set()
                if not self._background.wait(2000):
                    self.status_label.setText("正在取消，请等待当前操作结束后关闭。")
                    event.ignore()
                    return
            self.worker.stop()
        if self.project and self._dirty:
            if not self.save_project():
                event.ignore()
                return
        self.worker.stop()
        self._draft_timer.stop()
        self._save_text_state()
        self.player.stop()
        self.player.setSource(QUrl())
        event.accept()
