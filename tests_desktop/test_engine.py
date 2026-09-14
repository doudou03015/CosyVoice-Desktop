from contextlib import nullcontext
from pathlib import Path
import os
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from desktop_app.engine import CancelledError, Engine, PROMPT_PREFIX
from desktop_app.paths import session_dir
from tests_desktop.test_voices import tone


class Tensor:
    def __init__(self, data): self.data = np.asarray(data, dtype="float32")
    def detach(self): return self
    def float(self): return self
    def cpu(self): return self
    def reshape(self, *shape): self.data = self.data.reshape(*shape); return self
    def numpy(self): return self.data


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.previous_tempdir = tempfile.tempdir
        self.temp = tempfile.TemporaryDirectory(prefix="engine-test-", dir=session_dir())
        self.root = Path(self.temp.name)
        self.audio = tone(self.root / "参考.wav")
        self.events = []
        self.engine = Engine(self.root / "model", self.root / "session", self.events.append)
        self.engine.torch = SimpleNamespace(inference_mode=nullcontext,
                                            cuda=SimpleNamespace(empty_cache=lambda: None))
        self.calls = []
        def infer(text, transcript, audio, **kwargs):
            self.calls.append((text, transcript, audio, kwargs))
            yield {"tts_speech": Tensor(np.full(1000, .1))}
            yield {"tts_speech": Tensor(np.full(1400, .2))}
        self.engine.model = SimpleNamespace(sample_rate=24000, inference_zero_shot=infer,
            frontend=SimpleNamespace(text_normalize=lambda text, **kwargs: text.split("|")))
        self.module_patch = patch.dict(sys.modules, {"cosyvoice.utils.common": SimpleNamespace(set_all_random_seed=lambda seed: None)})
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()
        self.temp.cleanup()
        self.environment.stop()
        tempfile.tempdir = self.previous_tempdir

    def test_complete_all_chunks_and_reuse_identical_segments(self):
        result = self.engine.synthesize("First batch.|Second batch.", self.audio, "原文", self.root / "完整.wav")
        self.assertEqual(result["segments"], 2)
        self.assertEqual(sf.info(result["path"]).frames, 4800)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1], PROMPT_PREFIX + "原文")
        self.engine.synthesize("First batch.|Second batch.", self.audio, "原文", self.root / "复用.wav")
        self.assertEqual(len(self.calls), 2)
        cached = next((self.root / "session" / "segments").glob("*/*.wav"))
        cached.write_bytes(b"interrupted write")
        self.engine.synthesize("First batch.|Second batch.", self.audio, "原文", self.root / "修复.wav")
        self.assertEqual(len(self.calls), 3)

    def test_cancel_preserves_output_and_retry_uses_completed_segment(self):
        target = self.root / "保留.wav"
        target.write_bytes(b"existing-complete-result")
        cancel = threading.Event()
        def progress(event):
            if event.get("completed") == 1:
                cancel.set()
        self.engine.progress = progress
        with self.assertRaises(CancelledError):
            self.engine.synthesize("First batch.|Second batch.", self.audio, "原文", target, cancel=cancel)
        self.assertEqual(target.read_bytes(), b"existing-complete-result")
        self.assertEqual(len(self.calls), 1)
        self.engine.progress = self.events.append
        result = self.engine.synthesize("First batch.|Second batch.", self.audio, "原文", target)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sf.info(result["path"]).frames, 4800)

    def test_validation_rejects_bad_parameters_and_reference_overwrite(self):
        for speed, seed in ((True, 0), (float("nan"), 0), (2.1, 0), (1, -1), (1, 1.5), (1, True)):
            with self.subTest(speed=speed, seed=seed), self.assertRaises(ValueError):
                Engine.validate("文字", self.audio, "原文", speed, seed)
        with self.assertRaises(ValueError):
            self.engine.synthesize("文字", self.audio, "原文", self.audio)
        silent_phase = self.root / "反相.wav"
        sf.write(silent_phase, np.tile([.1, -.1], (16000, 1)), 16000, subtype="FLOAT")
        with self.assertRaises(ValueError):
            Engine.validate("文字", silent_phase, "原文", 1, 0)

    def test_silent_model_result_is_not_published_as_success(self):
        self.engine.model.inference_zero_shot = lambda *args, **kwargs: iter([
            {"tts_speech": Tensor(np.zeros(2400))}])
        output = self.root / "silent.wav"
        with self.assertRaises(RuntimeError):
            self.engine.synthesize("文字", self.audio, "原文", output)
        self.assertFalse(output.exists())

    def test_boundary_join_preserves_speech_raw_cache_and_exact_duration(self):
        from tests_desktop.test_audio_join import tone as speech, quiet
        batches = [np.concatenate((quiet(.25), speech(), quiet(1.4), speech(), quiet(.8))),
                   np.concatenate((quiet(.7), speech(frequency=337), quiet(.8))),
                   np.concatenate((quiet(.7), speech(frequency=441), quiet(.25)))]
        def infer(text, *args, **kwargs):
            self.calls.append(text)
            yield {"tts_speech": Tensor(batches[int(text)])}
        self.engine.model.inference_zero_shot = infer
        result = self.engine.synthesize("0|1|2", self.audio, "原文", self.root / "连接.wav")
        output, sr = sf.read(result["path"], dtype="float32")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(len(result["joins"]), 2)
        self.assertTrue(all(item["changed"] for item in result["joins"]))
        self.assertEqual(result["duration"], len(output) / sr)
        self.assertEqual(len(output), sum(map(len, batches)) - sum(
            j["left_trim_samples"] + j["right_trim_samples"] for j in result["joins"]))
        # The original beginning, internal sentence pause and final ending survive.
        np.testing.assert_allclose(output[:round(3.25*sr)], batches[0][:round(3.25*sr)], atol=1/32768)
        np.testing.assert_allclose(output[-round(1.25*sr):], batches[-1][-round(1.25*sr):], atol=1/32768)
        cached = sorted((self.root / "session" / "segments").glob("*/*.wav"))
        originals = [p.read_bytes() for p in cached]
        self.assertEqual([sf.info(p).frames for p in cached], list(map(len, batches)))
        retry = self.engine.synthesize("0|1|2", self.audio, "原文", self.root / "复用连接.wav")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([p.read_bytes() for p in cached], originals)
        self.assertEqual(retry["duration"], result["duration"])
        np.testing.assert_allclose(sf.read(retry["path"], dtype="float32")[0], output, atol=1/32768)

    def test_single_batch_keeps_all_original_pauses(self):
        from tests_desktop.test_audio_join import tone as speech, quiet
        waveform = np.concatenate((quiet(.9), speech(), quiet(1.5), speech(), quiet(.8)))
        self.engine.model.inference_zero_shot = lambda *args, **kwargs: iter([{"tts_speech": Tensor(waveform)}])
        result = self.engine.synthesize("单批文字", self.audio, "原文", self.root / "单批.wav")
        self.assertEqual(result["joins"], [])
        self.assertEqual(sf.info(result["path"]).frames, len(waveform))
        np.testing.assert_allclose(sf.read(result["path"], dtype="float32")[0], waveform, atol=1/32768)

    def test_cancel_after_join_never_publishes_partial_audio(self):
        target = self.root / "keep.wav"
        target.write_bytes(b"previous complete page")
        cancel = threading.Event()
        self.engine.progress = lambda event: cancel.set() if event.get("completed") == 2 else None
        with self.assertRaises(CancelledError):
            self.engine.synthesize("One.|Two.|Three.", self.audio, "原文", target, cancel=cancel)
        self.assertEqual(target.read_bytes(), b"previous complete page")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(list((self.root / "session").glob("speech-*.wav")))
        self.engine.progress = self.events.append
        result = self.engine.synthesize("One.|Two.|Three.", self.audio, "原文", target)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(sf.info(result["path"]).frames, 7200)

    def test_worker_reload_time_does_not_invalidate_completed_raw_batches(self):
        self.engine.runtime_info = {"torch": "2.3.1+cu121", "load_seconds": 24.77}
        result = self.engine.synthesize("One.|Two.", self.audio, "原文", self.root / "first.wav")
        self.engine.runtime_info["load_seconds"] = 31.5
        retry = self.engine.synthesize("One.|Two.", self.audio, "原文", self.root / "retry.wav")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(Path(result["path"]).read_bytes(), Path(retry["path"]).read_bytes())
        self.engine.runtime_info["torch"] = "2.7.1+cu128"
        self.engine.synthesize("One.|Two.", self.audio, "原文", self.root / "new-runtime.wav")
        self.assertEqual(len(self.calls), 4)


if __name__ == "__main__":
    unittest.main()
