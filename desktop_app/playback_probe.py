"""Exercise the installed preview path without loading the inference model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def run(arguments):
    parser = argparse.ArgumentParser(description="Check the desktop audio preview")
    parser.add_argument('--verify-playback', action='store_true')
    parser.add_argument('--audio', required=True)
    parser.add_argument('--report', required=True)
    options = parser.parse_args(arguments)
    from .paths import configure_worker_environment
    configure_worker_environment()
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer, qInstallMessageHandler
    from PySide6.QtMultimedia import QMediaDevices
    from PySide6.QtWidgets import QApplication

    messages, positions, errors, states = [], [], [], []
    previous_handler = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    app = QApplication([])
    from .ui import MainWindow
    window = MainWindow(verification=True)
    window.player.positionChanged.connect(positions.append)
    window.player.playbackStateChanged.connect(lambda value: states.append(value.name))
    window.player.errorOccurred.connect(lambda value, message: errors.append(message))
    result = {}

    def finish():
        result.update(
            complete=max(positions, default=0) >= 500 and not errors,
            positions_ms=positions.copy(), states=states.copy(), errors=errors.copy(),
            player_error=window.player.errorString(),
            media_status=window.player.mediaStatus().name,
            device=window.audio_output.device().description(),
            default_device=QMediaDevices.defaultAudioOutput().description(),
            volume=window.audio_output.volume(), muted=window.audio_output.isMuted(),
            display=window.playback_label.text(), qt_messages=messages.copy(),
        )
        window.stop_playback()
        app.quit()

    QTimer.singleShot(100, lambda: window.play(options.audio))
    QTimer.singleShot(3500, finish)
    try:
        app.exec()
    finally:
        window.worker.stop()
        if window._background:
            window._background.cancel_event.set()
            window._background.wait()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qInstallMessageHandler(previous_handler)
    destination = Path(options.report).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.get('complete') else 1
