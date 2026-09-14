"""Interactive installed-build microphone check; capture requires a button click."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil


def run(arguments):
    parser = argparse.ArgumentParser(description="通过按稿录音检查麦克风，并保存为本机自建音色。")
    parser.add_argument("--verify-recording", action="store_true")
    parser.add_argument("--report", required=True)
    options = parser.parse_args(arguments)
    from . import paths
    target = Path(options.report).expanduser().resolve()
    report = dict(complete=False, started_by_user=False)
    owned_session = paths._SESSION is None
    folder = dialog = app = None
    warning = None
    exit_code = 1
    try:
        folder = paths.configure_worker_environment()
        if os.name == "nt":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CosyVoice.Desktop.Workstation")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtGui import QFont, QIcon
        from PySide6.QtMultimedia import QMediaDevices
        from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
        from .recording import RecordingDialog
        from .ui import STYLE
        from .voices import VoiceLibrary, validate_reference

        app = QApplication.instance() or QApplication([])
        warning = QMessageBox.warning
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        app.setFont(QFont("Microsoft YaHei UI", 10))
        app.setWindowIcon(QIcon(str(paths.app_root() / "desktop_app/assets/app.ico")))
        report["data_dir"] = str(paths.user_data_dir())
        dialog = RecordingDialog(mode="library")
        dialog.setWindowTitle("麦克风验收 · 点击开始后按稿朗读")
        dialog.name_edit.setText("我的录音音色")

        def started():
            report["started_by_user"] = True
            selected_id = dialog.recorder._device_id
            report["device"] = next((item.description() for item in QMediaDevices.audioInputs()
                                      if bytes(item.id()) == selected_id), "所选输入设备")

        dialog.recorder.started.connect(started)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            recorded = dialog.take_result()
            voice = VoiceLibrary().add(recorded["name"], recorded["audio"], recorded["transcript"], source="软件内录制")
            stats = validate_reference(voice["audio"], voice["transcript"])
            report.update(complete=True, voice=voice, audio=stats)
            exit_code = 0
        else:
            report["cancelled"] = True
    except Exception as error:
        report["error"] = str(error)
        if warning is not None:
            warning(dialog, "录音检查未完成", str(error))
    finally:
        if dialog is not None:
            try:
                dialog.cleanup()
            except Exception as error:
                report.update(complete=False, cleanup_error=str(error))
                exit_code = 1
            finally:
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        try:
            paths.atomic_json(target, report)
        except (OSError, ValueError) as error:
            exit_code = 2
            if warning is not None:
                warning(None, "无法写出录音检查报告", str(error))
        finally:
            # Never remove a pre-existing session or a requested report inside it.
            if owned_session and folder is not None:
                resolved = paths.outside_sync(folder)
                temp_root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp"
                if (resolved.name.startswith("cosyvoice-desktop-") and temp_root.resolve() in resolved.parents
                        and target != resolved and resolved not in target.parents):
                    shutil.rmtree(resolved, ignore_errors=True)
                    if paths._SESSION == resolved:
                        paths._SESSION = None
    return exit_code
