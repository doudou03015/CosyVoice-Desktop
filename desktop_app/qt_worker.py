"""Qt process boundary for the inference runtime; this module never imports torch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .paths import app_root, session_dir


class InferenceProcess(QObject):
    event = Signal(dict)
    log = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.started.connect(self._dispatch)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self._buffer = b""
        self._stderr = b""
        self._pending: dict | None = None
        self.active: dict | None = None
        self.settings: dict = {}
        self._closing = False
        self._stopping = False
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._force_cancel)

    @property
    def busy(self):
        return self.active is not None

    def configure(self, settings: dict):
        if self.busy:
            raise RuntimeError("请先等待任务完成或取消，再修改运行设置。")
        keys = ("runtime_python", "model_dir", "gpu_uuid", "runtime_id")
        if any(self.settings.get(k) != settings.get(k) for k in keys):
            self.stop()
        self.settings = dict(settings)

    def submit(self, command: str, **values) -> str:
        if self._stopping:
            raise RuntimeError("正在停止上一个配音进程，请稍后重试。")
        if self.busy:
            raise RuntimeError("配音引擎正在处理任务，请稍候。")
        python = Path(self.settings.get("runtime_python", ""))
        model = Path(self.settings.get("model_dir", ""))
        if not python.is_file():
            raise ValueError("请在设置中安装运行组件，或选择有效的推理 Python 程序。")
        if not model.is_dir() or not (model / "cosyvoice3.yaml").is_file():
            raise ValueError("请在设置中下载模型，或选择包含 cosyvoice3.yaml 的模型目录。")
        request = dict(values, id=uuid4().hex, command=command)
        self.active = self._pending = request
        self.busy_changed.emit(True)
        self._closing = False
        if self.process.state() == QProcess.ProcessState.NotRunning:
            env = QProcessEnvironment.systemEnvironment()
            folder = session_dir()
            for key, value in {
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "TMP": str(folder), "TEMP": str(folder),
                "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1", "GRADIO_ANALYTICS_ENABLED": "False",
            }.items():
                env.insert(key, value)
            # The frozen GUI's _internal directory also holds Python 3.12
            # extensions. Inference uses its own Python 3.10 runtime, so its
            # stdlib and installed packages must precede the bundled sources.
            for key in env.keys():
                if key in ("PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX", "_MEIPASS2") or key.startswith(("_PYI_", "PYINSTALLER_")):
                    env.remove(key)
            if self.settings.get("gpu_uuid"):
                env.insert("CUDA_VISIBLE_DEVICES", self.settings["gpu_uuid"])
            ffmpeg = self.settings.get("ffmpeg_path", "")
            if ffmpeg:
                env.insert("PATH", str(Path(ffmpeg).parent) + os.pathsep + env.value("PATH"))
            self.process.setProcessEnvironment(env)
            self.process.setWorkingDirectory(str(folder))
            bootstrap = ("import runpy,sys; sys.path.append(sys.argv.pop(1)); "
                         "runpy.run_module('desktop_app.worker', run_name='__main__')")
            self.process.start(str(python), ["-B", "-s", "-u", "-c", bootstrap, str(app_root()),
                                           "--model-dir", str(model), "--session-dir", str(folder)])
        else:
            self._dispatch()
        return request["id"]

    def _dispatch(self):
        if self._pending:
            self._write(self._pending)
            self._pending = None

    def _write(self, request):
        self.process.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read_stdout(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeError):
                self.log.emit(line.decode("utf-8", "replace"))
                continue
            if not isinstance(value, dict):
                continue
            if value.get("code") == "control_failed":
                # A dead stdin reader cannot accept retries. Startup failures
                # may have no request id yet; attach them to our active task.
                request = self.active
                self.active = self._pending = None
                self._cancel_timer.stop()
                self.log.emit(value.get("message", "配音进程控制通道已关闭。"))
                self._terminate_tree()
                self.busy_changed.emit(False)
                if request:
                    self.event.emit(dict(value, id=request["id"]))
                continue
            if self.active and value.get("id") == self.active["id"]:
                if value.get("event") in ("result", "error", "cancelled"):
                    self._cancel_timer.stop()
                    self.active = None
                    self.busy_changed.emit(False)
                self.event.emit(value)
            elif value.get("event") == "progress":
                self.log.emit(value.get("message", ""))

    def _read_stderr(self):
        self._stderr += bytes(self.process.readAllStandardError())
        while b"\n" in self._stderr:
            line, self._stderr = self._stderr.split(b"\n", 1)
            self.log.emit(line.decode("utf-8", "replace").rstrip())

    def cancel(self):
        if self.active:
            self._write({"id": uuid4().hex, "command": "cancel", "target_id": self.active["id"]})
            self._cancel_timer.start(10_000)

    def _force_cancel(self):
        if self.active:
            request = self.active
            self.active = self._pending = None
            self._terminate_tree()
            self.busy_changed.emit(False)
            self.event.emit({"id": request["id"], "event": "cancelled",
                             "message": "任务已停止，完整结果已保留。下次生成将重新加载模型。"})

    def _finished(self, code, status):
        self._stopping = False
        self._read_stdout()
        self._read_stderr()
        self._buffer = self._stderr = b""
        if self.active:
            request = self.active
            self.active = self._pending = None
            self.busy_changed.emit(False)
            self.event.emit({"id": request["id"], "event": "error", "code": "worker_exit",
                             "message": f"配音进程已退出（代码 {code}），完整音频已保留。请查看运行日志后重试。"})

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.active:
            request = self.active
            self.active = self._pending = None
            self.busy_changed.emit(False)
            self.event.emit({"id": request["id"], "event": "error", "code": "start_failed",
                             "message": "无法启动推理环境：" + self.process.errorString()})

    def stop(self):
        self._closing = True
        self._cancel_timer.stop()
        self.active = self._pending = None
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._write({"id": uuid4().hex, "command": "shutdown"})
            if not self.process.waitForFinished(1000):
                self._terminate_tree()

    def _terminate_tree(self):
        """Kill only this process tree, including Windows venv launcher children."""
        self._stopping = True
        pid = int(self.process.processId())
        if os.name == "nt" and pid:
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                result = subprocess.run([str(taskkill), "/PID", str(pid), "/T", "/F"],
                                        capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode and self.process.state() != QProcess.ProcessState.NotRunning:
                    self.log.emit("停止配音进程树：" + result.stderr.decode("utf-8", "replace"))
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.log.emit("停止配音进程树失败：" + str(exc))
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._stopping = False
