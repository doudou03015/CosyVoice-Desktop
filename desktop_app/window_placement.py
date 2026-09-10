"""First-launch placement using Qt logical screen and frame coordinates."""
from __future__ import annotations

from PySide6.QtCore import QMargins, QRect, QSize
from PySide6.QtGui import QGuiApplication


def centered_client_geometry(available: QRect, preferred: QSize, frame: QMargins,
                             padding: int = 12) -> QRect:
    """Fit the complete window frame inside the primary screen's work area."""
    if available.isEmpty():
        raise ValueError("主屏幕没有可用的窗口区域。")
    horizontal = min(max(0, padding), max(0, (available.width() - frame.left() - frame.right() - 1) // 2))
    vertical = min(max(0, padding), max(0, (available.height() - frame.top() - frame.bottom() - 1) // 2))
    area = available.adjusted(horizontal, vertical, -horizontal, -vertical)
    width = min(max(1, preferred.width()), max(1, area.width() - frame.left() - frame.right()))
    height = min(max(1, preferred.height()), max(1, area.height() - frame.top() - frame.bottom()))
    outer_width = width + frame.left() + frame.right()
    outer_height = height + frame.top() + frame.bottom()
    x = area.x() + (area.width() - outer_width) // 2 + frame.left()
    y = area.y() + (area.height() - outer_height) // 2 + frame.top()
    return QRect(x, y, width, height)


def place_on_primary_screen(window, preferred: QSize | None = None) -> QRect | None:
    """Select the screen before sizing, so mixed-DPI frames use its scale."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    window.winId()  # Create the QWindow without displaying it.
    handle = window.windowHandle()
    if handle is not None:
        handle.setScreen(screen)
    frame = handle.frameMargins() if handle is not None else QMargins()
    geometry = centered_client_geometry(screen.availableGeometry(), preferred or QSize(1280, 850), frame)
    # Keep the usual minimum on a large display. A smaller logical work area
    # must take precedence; the page scroll area retains access to its content.
    window.setMinimumSize(min(1060, geometry.width()), min(740, geometry.height()))
    window.setGeometry(geometry)
    return geometry
