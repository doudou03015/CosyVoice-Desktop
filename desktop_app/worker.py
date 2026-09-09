"""Line-oriented subprocess protocol for the single CUDA synthesis worker."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import traceback

from .engine import CancelledError, Engine
from .paths import app_root, configure_worker_environment, session_dir


def command_lines(stream, closing):
    """Do not hold a blocking Windows CRT read lock while native DLLs initialize.

    NumPy/OpenBLAS DLL loading can deadlock with another thread inside stdin's
    blocking read on Windows. Peek the anonymous pipe and consume available bytes
    only. Other platforms and non-pipe input retain normal line iteration.
    """
    if os.name != "nt":
        yield from stream
        return
    try:
        import ctypes
        from ctypes import wintypes
        import msvcrt
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileType.restype = wintypes.DWORD
        if kernel.GetFileType(handle) != 3:
            yield from stream
            return
    except (AttributeError, OSError, ValueError):
        yield from stream
        return
    peek = kernel.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    pending = bytearray()
    while not closing.is_set():
        available = wintypes.DWORD()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232, 233):  # Broken/closed pipe.
                break
            raise OSError(error, "无法读取任务控制管道。")
        if not available.value:
            closing.wait(0.02)
            continue
        block = os.read(stream.fileno(), min(available.value, 65536))
        if not block:
            break
        pending.extend(block)
        while b"\n" in pending:
            raw, _, remainder = pending.partition(b"\n")
            pending = bytearray(remainder)
            yield raw.decode("utf-8", errors="replace")
        if len(pending) > 2 * 1024 * 1024:
            raise ValueError("请求超过允许大小。")
    if pending:
        yield pending.decode("utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--session-dir")
    args = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    session = configure_worker_environment(args.session_dir or session_dir())
    protocol_stdout = sys.stdout
    diagnostic_stderr = sys.stderr
    output_lock = threading.Lock()
    requests = queue.Queue()
    cancelled = threading.Event()
    closing = threading.Event()
    control_failed = threading.Event()
    active = {"id": None}
    state_lock = threading.Lock()

    def emit(event, request_id=None, **payload):
        message = {"id": active["id"] if request_id is None else request_id,
                   "event": event, **payload}
        with output_lock:
            protocol_stdout.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
            protocol_stdout.flush()

    def read_commands():
        try:
            for line in command_lines(sys.stdin, closing):
                try:
                    if len(line) > 2 * 1024 * 1024:
                        raise ValueError("请求超过允许大小。")
                    request = json.loads(line)
                    if not isinstance(request, dict) or not isinstance(request.get("command"), str):
                        raise ValueError("请求需要 command 字段。")
                    command = request["command"]
                    if command == "cancel":
                        with state_lock:
                            target = request.get("target_id")
                            if target is None or target == active["id"]:
                                cancelled.set()
                    elif command == "shutdown":
                        closing.set()
                        cancelled.set()
                        requests.put(None)
                        break
                    else:
                        requests.put(request)
                except (ValueError, TypeError) as exc:
                    emit("error", request_id="", code="invalid_request", message=str(exc))
        except Exception as exc:
            # A failed reader can no longer receive requests or cancellation.
            # Do not rely on threading.excepthook: report the complete traceback,
            # a structured terminal error, and a non-zero process exit.
            with state_lock:
                control_failed.set()
                closing.set()
                cancelled.set()
                failed_request = active["id"] or ""
            traceback.print_exc(file=diagnostic_stderr)
            diagnostic_stderr.flush()
            try:
                emit("error", request_id=failed_request, code="control_failed",
                     message="任务控制通道启动或读取失败，请修复运行组件后重试：" + str(exc))
            except Exception:
                # If the output pipe itself is broken, stderr and exit status
                # remain available to the parent; never hide the original cause.
                traceback.print_exc(file=diagnostic_stderr)
                diagnostic_stderr.flush()
        finally:
            requests.put(None)

    control_thread = threading.Thread(target=read_commands, name="worker-control", daemon=True)
    control_thread.start()
    engine = Engine(args.model_dir, session, progress=lambda data: emit("progress", **data))
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        # Some upstream libraries print during imports and inference. Keep every
        # such line on stderr, while emit() retains the original stdout handle.
        with redirect_stdout(sys.stderr):
            while not closing.is_set():
                request = requests.get()
                if request is None:
                    break
                with state_lock:
                    if control_failed.is_set():
                        break
                    active["id"] = request.get("id", "")
                    cancelled.clear()
                try:
                    command = request["command"]
                    if command == "load":
                        info = engine.load()
                        if control_failed.is_set():
                            break
                        if cancelled.is_set():
                            raise CancelledError("模型加载任务已取消。")
                        emit("result", runtime=info, message="模型已就绪。")
                    elif command in ("synthesize", "self_test"):
                        reference = request.get("ref_audio") or str(app_root() / "asset" / "zero_shot_prompt.wav")
                        transcript = request.get("ref_text") or "希望你以后能够做的比我还好呦。"
                        text = request.get("text") or ("你好，显卡计算和语音生成自检正在运行。" if command == "self_test" else "")
                        output = request.get("output_path") or str(session / "self-test.wav")
                        result = engine.synthesize(text, reference, transcript, output,
                                                   speed=request.get("speed", 1.0), seed=request.get("seed", 0),
                                                   cancel=cancelled)
                        if control_failed.is_set():
                            break
                        if command == "self_test":
                            result["self_test_passed"] = True
                        emit("result", **result)
                    else:
                        raise ValueError("不支持的工作进程命令：" + str(command))
                except CancelledError as exc:
                    if not control_failed.is_set():
                        emit("cancelled", message=str(exc))
                except Exception as exc:
                    traceback.print_exc(file=sys.stderr)
                    error_text = str(exc)
                    if type(exc).__name__ == "OutOfMemoryError" or "CUDA out of memory" in error_text:
                        code = "out_of_memory"
                        error_text = "显存不足。完整结果已保留，请关闭其他占显存程序，再点击重试。"
                    elif "no kernel image" in error_text or "not compatible" in error_text:
                        code = "incompatible_runtime"
                        error_text = "运行组件与所选显卡不兼容，请在设置中重新检测并安装对应组件。"
                    elif isinstance(exc, ValueError):
                        code = "invalid_input"
                    else:
                        code = "generation_failed"
                    if not control_failed.is_set():
                        emit("error", code=code, message=error_text)
                finally:
                    with state_lock:
                        active["id"] = None
    finally:
        with redirect_stdout(sys.stderr):
            engine.close()
    control_thread.join(timeout=1)
    return 2 if control_failed.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
