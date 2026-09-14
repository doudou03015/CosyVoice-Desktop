"""Entry point for both source and frozen desktop applications."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import json


def main(argv=None):
    arguments = list(sys.argv if argv is None else argv)
    if arguments[1:2] == ["--powerpoint-render"]:
        from .documents import render_cli
        return render_cli(arguments[2:])
    if "--verify-playback" in arguments:
        from .playback_probe import run
        return run(arguments[1:])
    if "--verify-recording" in arguments:
        from .recording_probe import run
        return run(arguments[1:])
    verify = "--verify-installation" in arguments
    verify_placement = "--verify-placement" in arguments
    report_path = None
    if verify or verify_placement:
        import argparse
        parser = argparse.ArgumentParser(description="CosyVoice 桌面安装自检")
        modes = parser.add_mutually_exclusive_group(required=True)
        modes.add_argument("--verify-installation", action="store_true")
        modes.add_argument("--verify-placement", action="store_true")
        parser.add_argument("--report", required=True)
        options = parser.parse_args(arguments[1:])
        report_path = Path(options.report).resolve()
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(3)
    from .paths import app_root, configure_worker_environment
    folder = configure_worker_environment()
    if os.name == "nt":
        import ctypes
        # Give native and source launches the same taskbar identity.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CosyVoice.Desktop.Workstation")
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    from . import APP_NAME, __version__
    app = QApplication(arguments)
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CosyVoice-Desktop")
    app.setWindowIcon(QIcon(str(app_root() / "desktop_app/assets/app.ico")))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(verification=verify or verify_placement)
    if verify:
        completed = False
        deadline = QTimer(window)
        deadline.setSingleShot(True)
        def finish(report):
            nonlocal completed
            if completed:
                return
            completed = True
            deadline.stop()
            final = dict(report, application="CosyVoice-Desktop", gpu=window.gpu_combo.currentData(),
                         runtime_id=window.settings.get("runtime_id"), logs=window.logs.toPlainText())
            try:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                app.exit(2)
                return
            app.exit(0 if report.get("complete") is True else 1)
        window.verification_finished.connect(finish)
        window.hardware_ready.connect(window.self_test)
        deadline.timeout.connect(lambda: finish({"complete": False, "error": "安装自检超过 5 分钟，已停止本次检查。"}))
        deadline.start(300_000)
    else:
        window.prepare_initial_placement()
        window.show()
        if verify_placement:
            def finish_placement():
                screen = app.primaryScreen()
                frame = window.frameGeometry()
                available = screen.availableGeometry() if screen else frame
                def rectangle(value):
                    return [value.x(), value.y(), value.width(), value.height()]
                final = {
                    "complete": bool(screen and window.screen() == screen and available.contains(frame)),
                    "screen": window.screen().name() if window.screen() else "",
                    "primary_screen": screen.name() if screen else "",
                    "available": rectangle(available), "frame": rectangle(frame),
                    "window_icon_present": not window.windowIcon().isNull(),
                    "window_title": window.windowTitle(),
                    "application_version": app.applicationVersion(),
                    "data_directory": str(window.library.root.parent),
                    "voice_ids": [voice["id"] for voice in window.voice_items],
                }
                try:
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
                except OSError:
                    app.exit(2)
                    return
                app.exit(0 if final["complete"] else 1)
            QTimer.singleShot(300, window, finish_placement)
    try:
        return app.exec()
    finally:
        if window._background:
            window._background.cancel_event.set()
            window._background.wait()
        window.worker.stop()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        from .paths import outside_sync
        resolved = outside_sync(folder)
        if resolved.name.startswith("cosyvoice-desktop-") and resolved.parent.name.lower() == "temp":
            shutil.rmtree(resolved, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
