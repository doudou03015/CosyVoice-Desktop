"""Native child-tree cancellation regression for Windows venv launchers."""
import ctypes
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    ctypes.windll.kernel32.SetErrorMode(3)

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QProcess
from PySide6.QtWidgets import QApplication

from desktop_app.qt_worker import InferenceProcess
from desktop_app import qt_worker


@pytest.mark.parametrize("reported_id", ["", "active-request"])
def test_control_failure_disposes_process_before_terminal_event(monkeypatch, reported_id):
    app = QApplication.instance() or QApplication([])
    manager = InferenceProcess()
    manager.active = manager._pending = {"id": "active-request", "command": "self_test"}
    actions = []
    monkeypatch.setattr(manager.process, "readAllStandardOutput", lambda: b"")
    monkeypatch.setattr(manager, "_terminate_tree", lambda: actions.append(("terminate", manager.active)))
    manager.event.connect(lambda event: actions.append(("event", event)))
    manager._buffer = (json.dumps({"id": reported_id, "event": "error", "code": "control_failed", "message": "reader failed"}) + "\n").encode()
    try:
        manager._read_stdout()
        assert actions[0] == ("terminate", None)
        assert actions[1][0] == "event"
        assert actions[1][1]["id"] == "active-request"
        assert actions[1][1]["code"] == "control_failed"
        assert manager._pending is None
        assert not manager.busy
    finally:
        manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("python", list(dict.fromkeys([
    sys.executable, os.environ.get("COSYVOICE_TEST_RUNTIME_PYTHON", sys.executable)
])))
def test_external_runtime_imports_its_own_extensions_before_frozen_sources(tmp_path, monkeypatch, python):
    """A GUI bundle may contain native modules built for another Python ABI."""
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "frozen _internal"
    package = source / "desktop_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    # These names reproduce the frozen bundle contamination without loading an
    # incompatible DLL or displaying a Windows native crash dialog.
    for name in ("_ctypes", "socket"):
        (source / (name + ".py")).write_text("raise RuntimeError('GUI extension shadowed inference runtime')\n", encoding="utf-8")
    (package / "worker.py").write_text(
        "import ctypes, _ctypes, socket, json, os, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request['command'] == 'shutdown': break\n"
        "    result = dict(id=request['id'],event='result',cwd=os.getcwd(),"
        "extension=_ctypes.__file__,socket=socket.__file__,"
        "environment={key:value for key,value in os.environ.items() if key.startswith(('_PYI_', 'PYINSTALLER_')) or key in ('PYTHONPATH','PYTHONHOME','_MEIPASS2')})\n"
        "    print(json.dumps(result),flush=True)\n", encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    (model / "cosyvoice3.yaml").write_text("", encoding="utf-8")
    session = tmp_path / "worker session"
    session.mkdir()
    monkeypatch.setattr(qt_worker, "app_root", lambda: source)
    monkeypatch.setattr(qt_worker, "session_dir", lambda: session)
    for key in ("PYTHONPATH", "PYTHONHOME", "_PYI_APPLICATION_HOME_DIR", "PYINSTALLER_RESET_ENVIRONMENT", "_MEIPASS2"):
        monkeypatch.setenv(key, str(source))
    manager = InferenceProcess()
    manager.configure({"runtime_python": python, "model_dir": str(model)})
    events = []
    logs = []
    manager.event.connect(events.append)
    manager.log.connect(logs.append)
    try:
        manager.submit("probe")
        deadline = time.monotonic() + 15
        while not events and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert events, logs
        assert events[-1]["event"] == "result", (events, logs)
        assert Path(events[-1]["cwd"]) == session
        assert source not in Path(events[-1]["extension"]).parents
        assert source not in Path(events[-1]["socket"]).parents
        assert events[-1]["environment"] == {}
    finally:
        manager.stop()
        manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree regression")
def test_force_cancel_stops_only_owned_process_tree(tmp_path):
    app = QApplication.instance() or QApplication([])
    manager = InferenceProcess()
    marker = tmp_path / "child.pid"
    script = ("import subprocess,sys,time; from pathlib import Path; "
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW); "
              f"Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)")
    manager.process.start(sys.executable, ["-B", "-c", script])
    assert manager.process.waitForStarted(5000)
    try:
        for _ in range(100):
            if marker.is_file():
                break
            app.processEvents()
            time.sleep(.02)
        assert marker.is_file()
        pid = int(marker.read_text())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        assert handle
        try:
            manager.active = {"id": "cancel-me", "command": "synthesize"}
            events = []
            manager.event.connect(events.append)
            manager._force_cancel()
            app.processEvents()
            result = ctypes.c_ulong()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(result))
            assert result.value != 259  # STILL_ACTIVE
            assert manager.process.state() == QProcess.ProcessState.NotRunning
            assert not manager._stopping
            assert events[-1]["event"] == "cancelled"
        finally:
            kernel.CloseHandle(handle)
    finally:
        manager._terminate_tree()
        manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
