from pathlib import Path
import json
import shutil
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
        library.delete(presets[0]["id"])
        self.assertTrue(Path(presets[0]["audio"]).exists())
        self.assertNotIn(presets[0]["id"], [item["id"] for item in library.list()])
        restored_library = VoiceLibrary(library.root)
        self.assertEqual([item["id"] for item in restored_library.hidden_presets()], [presets[0]["id"]])
        restored_library.restore_presets([presets[0]["id"]])
        self.assertEqual(len(restored_library.list()), 6)

    def test_all_presets_can_be_hidden_without_affecting_custom_voices(self):
        library = VoiceLibrary(self.root / "库")
        original = library.presets()
        for item in original:
            library.delete(item["id"])
        self.assertEqual(library.list(), [])
        custom = library.add("麦克风录制", self.audio, "录音原文", source="软件内录制")
        self.assertEqual(library.list()[0]["source"], "软件内录制")
        library.rename(custom["id"], "我的音色")
        self.assertEqual(len(library.hidden_presets()), len(original))
        library.restore_presets([original[1]["id"]])
        self.assertEqual([item["id"] for item in library.list()], [original[1]["id"], custom["id"]])
        with self.assertRaises(ValueError):
            library.restore_presets(["not-a-preset"])

    def test_local_presets_first_deduplicated_and_hidden_ids_survive_updates(self):
        library = VoiceLibrary(self.root / "库")
        existing = library.presets()[0]
        library.local_presets.mkdir()
        shutil.copyfile(self.audio, library.local_presets / "reference.wav")
        rows = [dict(id="cosyvoice_demo_longwan_zh", name="龙婉", reference_audio="reference.wav",
                     transcript="测试录音", distribution="local_only"),
                dict(id=existing["id"], name="更新的预设", reference_audio="reference.wav",
                     transcript="测试录音")]
        manifest = library.local_presets / "manifest.json"
        manifest.write_text(json.dumps(dict(schema_version=1, voices=rows)), encoding="utf-8")
        self.assertEqual(len(library.list()), 7)
        self.assertEqual(library.list()[0]["distribution"], "local_only")
        library.delete(rows[0]["id"])
        rows[0]["name"] = "更新名称"
        manifest.write_text(json.dumps(dict(schema_version=1, voices=rows)), encoding="utf-8")
        self.assertNotIn(rows[0]["id"], [item["id"] for item in VoiceLibrary(library.root).list()])
        library.restore_presets([rows[0]["id"]])
        self.assertEqual(library.list()[0]["name"], "更新名称")

    def test_local_preset_paths_cannot_escape_manifest_directory(self):
        library = VoiceLibrary(self.root / "库")
        library.local_presets.mkdir()
        data = dict(schema_version=1, voices=[dict(id="escaped", name="不可使用", transcript="原文",
                    reference_audio=str(self.audio.resolve()), demo_audio=str(self.audio.resolve()))])
        (library.local_presets / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
        self.assertNotIn("escaped", [item["id"] for item in library.presets()])

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
