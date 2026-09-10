"""Logical-coordinate placement and first-show lifecycle regressions."""
import os
from types import SimpleNamespace

if os.name == "nt":
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(3)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMargins, QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication

from desktop_app import paths, ui, window_placement


@pytest.mark.parametrize("available", [
    QRect(0, 0, 2293, 912),
    QRect(-1080, 0, 720, 1232),
    QRect(3440, 0, 1536, 816),
    QRect(100, -900, 960, 600),
    QRect(0, 48, 800, 512),
    QRect(-300, -200, 320, 240),
])
def test_complete_frame_stays_centered_inside_work_area(available):
    margins = QMargins(8, 31, 8, 8)
    client = window_placement.centered_client_geometry(available, QSize(1280, 850), margins)
    frame = client.marginsAdded(margins)
    assert available.contains(frame)
    assert abs(frame.center().x() - available.center().x()) <= 1
    assert abs(frame.center().y() - available.center().y()) <= 1
    assert frame.top() >= available.top() + 12
    assert frame.bottom() <= available.bottom() - 12


def test_primary_is_selected_before_using_its_frame_geometry(monkeypatch):
    events = []
    primary = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 2293, 912))
    secondary = SimpleNamespace(availableGeometry=lambda: QRect(-1080, 0, 720, 1232))

    class Handle:
        screen = secondary

        def setScreen(self, screen):
            self.screen = screen
            events.append("screen")

        def frameMargins(self):
            assert self.screen is primary
            events.append("frame")
            return QMargins(8, 31, 8, 8)

    handle = Handle()
    received = {}
    window = SimpleNamespace(
        winId=lambda: events.append("native"), windowHandle=lambda: handle,
        setMinimumSize=lambda w, h: received.update(minimum=QSize(w, h)),
        setGeometry=lambda rect: received.update(geometry=rect))
    monkeypatch.setattr(window_placement, "QGuiApplication", SimpleNamespace(primaryScreen=lambda: primary))
    result = window_placement.place_on_primary_screen(window)
    assert events == ["native", "screen", "frame"]
    assert handle.screen is primary
    assert result.x() > 0  # The negative-coordinate secondary was not chosen.
    assert result.width() == 1280  # Already logical; no DPI multiplication.
    assert received["minimum"] == QSize(1060, 740)


def test_small_screen_reduces_minimum_below_original_740(monkeypatch):
    screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 960, 600))
    handle = SimpleNamespace(setScreen=lambda value: None, frameMargins=lambda: QMargins(8, 31, 8, 8))
    received = {}
    window = SimpleNamespace(winId=lambda: None, windowHandle=lambda: handle,
                             setMinimumSize=lambda w, h: received.update(width=w, height=h),
                             setGeometry=lambda rect: received.update(rect=rect))
    monkeypatch.setattr(window_placement, "QGuiApplication", SimpleNamespace(primaryScreen=lambda: screen))
    window_placement.place_on_primary_screen(window)
    assert received["width"] < 1060
    assert received["height"] < 740
    assert screen.availableGeometry().contains(received["rect"].marginsAdded(handle.frameMargins()))


def test_first_show_corrects_frame_once_and_small_content_scrolls(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(tmp_path / "data"))
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setattr(paths, "_SESSION", session)
    monkeypatch.setattr(ui.MainWindow, "detect_hardware", lambda self: None)
    placements = []
    available = QRect(0, 0, 900, 600)

    def place(window):
        window.winId()
        frame = window.windowHandle().frameMargins()
        rect = window_placement.centered_client_geometry(available, QSize(1280, 850), frame)
        window.setMinimumSize(min(1060, rect.width()), min(740, rect.height()))
        window.setGeometry(rect)
        placements.append(rect)

    monkeypatch.setattr(ui, "place_on_primary_screen", place)
    window = ui.MainWindow()
    try:
        window.prepare_initial_placement()
        assert len(placements) == 1
        window.show()
        app.processEvents()
        assert len(placements) == 2
        assert available.contains(window.frameGeometry())
        assert window.page_scroll.verticalScrollBar().maximum() > 0
        for widget in (window.playback_label, window.status_label, window.cancel_button):
            bottom = widget.mapTo(window, QPoint(0, 0)).y() + widget.height()
            assert bottom <= window.height()
        window.move(70, 60)
        moved = window.pos()
        window.hide()
        window.show()
        app.processEvents()
        assert len(placements) == 2
        assert window.pos() == moved
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
