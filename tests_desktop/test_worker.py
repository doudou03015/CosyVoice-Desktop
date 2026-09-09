import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

from desktop_app.paths import app_root, session_dir


BOOTSTRAP = '''
import json, os, threading, time
from desktop_app import worker
from desktop_app.engine import CancelledError
started=threading.Event()
class FakeEngine:
    def __init__(self, model, session, progress): self.progress=progress
    def load(self): print("third party banner"); return {"gpu":"test"}
    def synthesize(self,text,audio,transcript,output,**kwargs):
        import numpy
        self.progress({"stage":"synthesizing","message":"native import complete","completed":0,"total":1})
        started.set()
        if text == "oom": raise RuntimeError("CUDA out of memory")
        if text == "wait":
            for _ in range(500):
                if kwargs["cancel"].is_set(): raise CancelledError("cancelled")
                time.sleep(.01)
        return {"path":output,"sample_rate":24000,"duration":1,"segments":1}
    def close(self): pass
worker.Engine=FakeEngine
if os.environ.get("COSYVOICE_TEST_CONTROL_FAILURE"):
    def failed_lines(stream,closing):
        if os.environ["COSYVOICE_TEST_CONTROL_FAILURE"] == "active":
            yield json.dumps({"id":"active-failure","command":"synthesize","text":"wait"})
            assert started.wait(5), "test synthesis never started"
        raise ImportError("DLL load failed while importing _ctypes: simulated ABI mismatch")
    worker.command_lines=failed_lines
raise SystemExit(worker.main())
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-test-", dir=session_dir())
        self.root = Path(self.temp.name)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(app_root()))
        runtime_python = os.environ.get("COSYVOICE_TEST_WORKER_PYTHON", sys.executable)
        self.process = subprocess.Popen([runtime_python, "-B", "-u", "-c", BOOTSTRAP,
            "--model-dir", str(self.root), "--session-dir", str(self.root / "session")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, cwd=app_root())
        self.events = queue.Queue()
        self.errors = []
        def read():
            for line in self.process.stdout:
                try: self.events.put(json.loads(line))
                except ValueError: self.events.put({"event":"bad_protocol","line":line})
        def errors():
            for line in self.process.stderr: self.errors.append(line)
        self.readers = [threading.Thread(target=read, daemon=True), threading.Thread(target=errors, daemon=True)]
        for thread in self.readers: thread.start()

    def tearDown(self):
        if self.process.poll() is None:
            self.send({"command": "shutdown"})
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr): stream.close()
        for thread in self.readers: thread.join(timeout=1)
        self.temp.cleanup()

    def send(self, request):
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def next(self):
        return self.events.get(timeout=15)

    def terminal(self):
        while True:
            value = self.next()
            if value["event"] != "progress": return value

    def test_invalid_json_recovers_and_library_prints_stay_off_protocol(self):
        self.process.stdin.write("bad-json\n")
        self.process.stdin.flush()
        self.assertEqual(self.terminal()["code"], "invalid_request")
        self.send({"id":"load", "command":"load"})
        result = self.terminal()
        self.assertEqual((result["id"], result["event"]), ("load", "result"))
        self.send({"id":"bad", "command":"unsupported"})
        self.assertEqual(self.terminal()["code"], "invalid_input")

    def test_native_import_after_reader_start_cancel_then_retry_and_oom(self):
        self.send({"id":"cancel", "command":"synthesize", "text":"wait"})
        self.assertEqual(self.next()["stage"], "synthesizing")
        self.send({"command":"cancel", "target_id":"previous-task"})
        with self.assertRaises(queue.Empty):
            self.events.get(timeout=.1)
        self.send({"command":"cancel", "target_id":"cancel"})
        self.assertEqual(self.terminal()["event"], "cancelled")
        self.send({"id":"retry", "command":"self_test"})
        result = self.terminal()
        self.assertEqual(result["id"], "retry")
        self.assertTrue(result["self_test_passed"])
        self.send({"id":"oom", "command":"synthesize", "text":"oom"})
        self.assertEqual(self.terminal()["code"], "out_of_memory")


class WorkerControlFailureTests(unittest.TestCase):
    def test_native_import_error_in_reader_is_visible_and_exit_is_failure(self):
        with tempfile.TemporaryDirectory(prefix="worker-failure-test-", dir=session_dir()) as directory:
            root = Path(directory)
            for stage, expected_id in (("startup", ""), ("active", "active-failure")):
                with self.subTest(stage=stage):
                    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(app_root()),
                               COSYVOICE_TEST_CONTROL_FAILURE=stage)
                    runtime = os.environ.get("COSYVOICE_TEST_WORKER_PYTHON", sys.executable)
                    result = subprocess.run([runtime, "-B", "-u", "-c", BOOTSTRAP,
                        "--model-dir", str(root), "--session-dir", str(root / stage)],
                        input="", capture_output=True, text=True, encoding="utf-8", env=env,
                        cwd=app_root(), timeout=15)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("Traceback (most recent call last)", result.stderr)
                    self.assertIn("ImportError: DLL load failed while importing _ctypes", result.stderr)
                    self.assertNotIn("Exception in thread", result.stderr)
                    events = [json.loads(line) for line in result.stdout.splitlines()]
                    terminal = [event for event in events if event["event"] != "progress"]
                    self.assertEqual(len(terminal), 1)
                    self.assertEqual((terminal[0]["id"], terminal[0]["code"]), (expected_id, "control_failed"))


if __name__ == "__main__":
    unittest.main()
