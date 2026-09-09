from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from desktop_app.paths import session_dir
from desktop_app.voices import VoiceLibrary, temporary_voice, validate_reference


def tone(path, frames=24000, rate=24000):
    data = 0.15 * np.sin(np.arange(frames) * (2 * np.pi * 220 / rate))
    sf.write(path, data, rate, subtype="PCM_16")
    return path


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="voices-test-", dir=session_dir())
        self.root = Path(self.temp.name)
        self.audio = tone(self.root / "录音.wav")

    def tearDown(self):
        self.temp.cleanup()

    def test_presets_custom_and_temporary_are_distinct(self):
        library = VoiceLibrary(self.root / "库")
        presets = [v for v in library.list() if v["kind"] == "preset"]
        self.assertEqual(len(presets), 6)
        for voice in presets:
            validate_reference(voice["audio"], voice["transcript"])
            self.assertNotIn("inference_validation", voice)
        temporary = temporary_voice(self.audio, "录音原文")
        self.assertEqual(temporary["kind"], "temporary")
        voice = library.add("我的声音", self.audio, "录音原文")
        self.assertNotEqual(Path(voice["audio"]), self.audio)
        self.assertEqual(library.rename(voice["id"], "新版音色")["name"], "新版音色")
        library.delete(voice["id"])
        self.assertTrue(self.audio.exists())
        self.assertFalse(Path(voice["audio"]).exists())
        with self.assertRaises(ValueError):
            library.delete(presets[0]["id"])

    def test_rejects_invalid_audio_and_transcript(self):
        with self.assertRaises(ValueError):
            validate_reference(self.audio, " ")
        for name, data, rate in (("silent", np.zeros(24000), 24000),
                                 ("short_rate", np.ones(8000) * 0.1, 8000),
                                 ("too_long", np.ones(31 * 16000) * 0.1, 16000),
                                 ("nan", np.full(16000, np.nan), 16000),
                                 ("phase", np.tile([0.1, -0.1], (16000, 1)), 16000)):
            path = self.root / (name + ".wav")
            sf.write(path, data, rate, subtype="FLOAT")
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_reference(path, "原文")


if __name__ == "__main__":
    unittest.main()
