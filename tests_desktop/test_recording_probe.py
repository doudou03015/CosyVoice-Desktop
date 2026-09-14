"""Installed recording-check flow using a fake microphone and isolated data."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QMessageBox

from desktop_app import main, paths, recording, recording_probe, voices
from tests_desktop.test_recording import capture_env, pcm


@pytest.fixture
def probe_env(capture_env, monkeypatch):
    folder = capture_env.folder / "probe-session"
    folder.mkdir()
    monkeypatch.setattr(paths, "configure_worker_environment", lambda: folder)
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(capture_env.folder / "isolated-data"))
    capture_env.report = capture_env.folder / "report.json"
    capture_env.warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, message: capture_env.warnings.append((title, message)))
    return capture_env


def execute_with_action(monkeypatch, action):
    native_exec = recording.RecordingDialog.exec

    def execute(dialog):
        QTimer.singleShot(0, dialog, lambda: action(dialog))
        return native_exec(dialog)

    monkeypatch.setattr(recording.RecordingDialog, "exec", execute)


def run_probe(env):
    return recording_probe.run(["--verify-recording", "--report", str(env.report)])


def test_cancel_without_start_does_not_open_capture_and_writes_report(probe_env, monkeypatch):
    execute_with_action(monkeypatch, lambda dialog: dialog.reject())
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["cancelled"] and not report["complete"] and not report["started_by_user"]
    assert not probe_env.source.instances
    assert not list(probe_env.folder.glob("recording-*.wav"))
    assert not (probe_env.folder / "isolated-data" / "voices").exists()


def test_close_during_synthetic_capture_releases_input_and_reports_cancel(probe_env, monkeypatch):
    def action(dialog):
        dialog.start_button.click()  # AudioRecorder uses the fake source fixture.
        probe_env.source.instances[-1].stream.push(pcm(1))
        dialog.close()

    execute_with_action(monkeypatch, action)
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["started_by_user"] and report["cancelled"] and not report["complete"]
    assert probe_env.source.instances[-1].stops == 1
    assert not list(probe_env.folder.glob("recording-*.wav"))


def accepted_recording(env, dialog):
    dialog.start_button.click()
    env.source.instances[-1].stream.push(pcm())
    dialog.stop_button.click()
    dialog.accept()


def test_accepted_recording_saved_before_temporary_cleanup(probe_env, monkeypatch):
    execute_with_action(monkeypatch, lambda dialog: accepted_recording(probe_env, dialog))
    assert run_probe(probe_env) == 0
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["complete"] and report["started_by_user"]
    assert report["recording_validated"] and report["recording"]["duration"] == 3.25
    voice = report["voice"]
    assert Path(voice["audio"]).is_file()
    assert voice["source"] == "软件内录制" and report["audio"]["duration"] == 3.25
    assert not list(probe_env.folder.glob("recording-*.wav"))
    assert voices.VoiceLibrary().get(voice["id"])["audio"] == voice["audio"]


def test_library_save_failure_is_reported_and_capture_is_cleaned(probe_env, monkeypatch):
    execute_with_action(monkeypatch, lambda dialog: accepted_recording(probe_env, dialog))

    def fail_add(self, *args, **kwargs):
        raise OSError("模拟音色库写入失败")

    monkeypatch.setattr(voices.VoiceLibrary, "add", fail_add)
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert not report["complete"] and report["error"] == "模拟音色库写入失败"
    assert report["started_by_user"] and report["recording_validated"]
    assert report["recording"]["duration"] == 3.25 and len(report["recording"]["sha256"]) == 64
    assert report["recording_transcript"] == recording.DEFAULT_TRANSCRIPT
    assert report["recording_name"] == "我的录音音色"
    assert not list(probe_env.folder.glob("recording-*.wav"))
    assert probe_env.warnings


def test_post_save_lookup_failure_preserves_library_audio_and_capture_evidence(probe_env, monkeypatch):
    execute_with_action(monkeypatch, lambda dialog: accepted_recording(probe_env, dialog))

    def lookup_failure(self, voice_id):
        raise ValueError("模拟保存后读取路径失败")

    monkeypatch.setattr(voices.VoiceLibrary, "get", lookup_failure)
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["recording_validated"] and not report["complete"]
    assert report["error"] == "模拟保存后读取路径失败"
    saved = list((probe_env.folder / "isolated-data" / "voices").glob("custom-*.wav"))
    assert len(saved) == 1
    stats = voices.validate_reference(saved[0], report["recording_transcript"])
    assert stats["sha256"] == report["recording"]["sha256"]


def test_setup_failure_still_writes_failure_report(probe_env, monkeypatch):
    def denied():
        raise PermissionError("模拟数据目录不可访问")

    monkeypatch.setattr(paths, "user_data_dir", denied)
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["error"] == "模拟数据目录不可访问" and not report["complete"]
    assert not probe_env.source.instances


def test_cleanup_error_does_not_prevent_report(probe_env, monkeypatch):
    def execute(dialog):
        def fail_cleanup():
            raise OSError("模拟临时文件清理失败")
        dialog.cleanup = fail_cleanup
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(recording.RecordingDialog, "exec", execute)
    assert run_probe(probe_env) == 1
    report = json.loads(probe_env.report.read_text(encoding="utf-8"))
    assert report["cleanup_error"] == "模拟临时文件清理失败"
    assert report["cancelled"] and not report["complete"]


def test_report_write_failure_returns_distinct_exit_code(probe_env, monkeypatch):
    execute_with_action(monkeypatch, lambda dialog: dialog.reject())

    def denied(*args):
        raise PermissionError("模拟报告目录不可写")

    monkeypatch.setattr(paths, "atomic_json", denied)
    assert run_probe(probe_env) == 2
    assert not probe_env.report.exists()
    assert probe_env.warnings[-1][0] == "无法写出录音检查报告"
    assert not probe_env.source.instances


def test_only_new_owned_session_is_removed(probe_env, monkeypatch):
    profile = probe_env.folder / "isolated-profile"
    temp_root = profile / "Temp"
    temp_root.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(profile))
    folder = temp_root / "cosyvoice-desktop-probe-owned"
    monkeypatch.setattr(paths, "_SESSION", None)

    def configure():
        folder.mkdir()
        paths._SESSION = folder
        (folder / "cache").mkdir()
        return folder

    monkeypatch.setattr(paths, "configure_worker_environment", configure)
    execute_with_action(monkeypatch, lambda dialog: dialog.reject())
    assert run_probe(probe_env) == 1
    assert probe_env.report.is_file()
    assert not folder.exists() and paths._SESSION is None


def test_main_dispatches_recording_arguments_without_starting_main_gui(monkeypatch):
    seen = []
    monkeypatch.setattr(recording_probe, "run", lambda args: seen.append(args) or 7)
    assert main.main(["CosyVoice-Desktop.exe", "--verify-recording", "--report", "check.json"]) == 7
    assert seen == [["--verify-recording", "--report", "check.json"]]
