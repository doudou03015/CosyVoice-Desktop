import json
from pathlib import Path
import shutil
import tempfile
import unittest

from desktop_app.paths import session_dir
from desktop_app.projects import (cached_audio, create_project, effective_settings, fingerprint,
                                  load_project, remember_audio, resolve_resource, save_project)
from desktop_app.voices import VoiceLibrary
from tests_desktop.test_documents import create_presentation
from tests_desktop.test_voices import tone


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="projects-test-", dir=session_dir())
        self.root = Path(self.temp.name)
        self.source = create_presentation(self.root / "原课件.pptx", count=2)
        self.audio = tone(self.root / "原录音.wav")
        self.library = VoiceLibrary(self.root / "音色库")
        self.voice = self.library.add("声音", self.audio, "这是一段原文。")
        self.directory = self.root / "我的 工程"

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_survives_voice_deletion_and_project_relocation(self):
        project = create_project(self.source, self.directory, self.voice)
        self.library.delete(self.voice["id"])
        saved = (self.directory / "project.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), saved)
        self.assertFalse(Path(project["source"]).is_absolute())
        self.source.unlink()
        moved = self.root / "移动后的工程"
        shutil.copytree(self.directory, moved)
        reopened = load_project(moved)
        self.assertTrue(Path(resolve_resource(moved, reopened["source"])).exists())
        audio = reopened["voices"][self.voice["id"]]["audio"]
        self.assertTrue(Path(resolve_resource(moved, audio)).exists())

    def test_settings_cache_invalidation_corruption_and_slide_identity(self):
        project = create_project(self.source, self.directory, self.voice)
        slide = project["slides"][0]
        self.assertEqual(effective_settings(project, slide)["speed"], 1)
        project["settings"]["speed"] = 1.2
        self.assertEqual(effective_settings(project, slide)["speed"], 1.2)
        slide["speed"] = 0.8
        self.assertEqual(effective_settings(project, slide)["speed"], 0.8)
        key = fingerprint(slide["text"], self.voice, 0.8, 0, "model", "cu121")
        remember_audio(project, slide, self.directory, self.audio, key)
        self.assertIs(slide, project["slides"][0])
        self.assertTrue(cached_audio(slide, self.directory, key))
        self.assertIsNone(cached_audio(slide, self.directory, "changed"))
        self.assertNotEqual(key, fingerprint(slide["text"] + "新内容", self.voice, .8, 0, "model", "cu121"))
        output = Path(resolve_resource(self.directory, slide["audio"]))
        with output.open("ab") as stream:
            stream.write(b"corruption")
        self.assertIsNone(cached_audio(slide, self.directory, key))
        output.unlink()
        self.assertEqual(load_project(self.directory)["slides"][0]["status"], "missing")

    def test_untrusted_project_cannot_escape_directory(self):
        for value in ("../outside.wav", str(self.root / "outside.wav")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_resource(self.directory, value)
        project = create_project(self.source, self.directory, self.voice)
        project["schema_version"] = 999
        (self.directory / "project.json").write_text(json.dumps(project), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_project(self.directory)


if __name__ == "__main__":
    unittest.main()
