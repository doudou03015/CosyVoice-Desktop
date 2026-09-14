"""Local import preserves deletion choices and never mutates the public catalog."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from desktop_app import paths
from desktop_app.documents import sha256
from desktop_app.voices import VoiceLibrary

spec = importlib.util.spec_from_file_location(
    "local_demo_import", Path(__file__).resolve().parents[1] / "packaging/import_demo_voices.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


@pytest.fixture
def import_context(tmp_path, monkeypatch):
    # Recreate Windows Temp containment without touching the user's data.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(tmp_path / "unused"))
    monkeypatch.setattr(paths, "_SESSION", tmp_path / "session")
    (tmp_path / "session").mkdir()
    return tmp_path / "user", tmp_path / "Temp/task-download"


def synthetic_download(url, path):
    assert url in [importer.BASE_URL + row[2] for row in importer.SAMPLES]
    frames = np.arange(24000 * 4)
    signal = 0.12 * np.sin(frames * 2 * np.pi * 220 / 24000)
    sf.write(path, np.column_stack([signal, signal * 0.8]), 24000, subtype="PCM_24")


def test_local_import_is_idempotent_keeps_hidden_ids_and_demo(import_context):
    data, stage = import_context
    public = paths.app_root() / "voice_library/manifest.json"
    public_hash = sha256(public)
    manifest = importer.install_samples(data, stage, fetch=synthetic_download)
    library = VoiceLibrary(data / "voices")
    expected = [row[0] for row in importer.SAMPLES]
    assert [row["id"] for row in library.list()[:3]] == expected
    library.delete(expected[1])
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    catalog["voices"][0]["demo_audio"] = expected[0] + "/demo.wav"
    catalog["voices"][0]["inference_validation"] = {"success": True}
    manifest.write_text(json.dumps(catalog), encoding="utf-8")
    importer.install_samples(data, stage, fetch=synthetic_download)
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(catalog["voices"]) == 3
    assert catalog["voices"][0]["inference_validation"]["success"]
    assert [row["id"] for row in library.hidden_presets()] == [expected[1]]
    for row in catalog["voices"]:
        assert row["distribution"] == "local_only"
        assert row["redistribution_approved"] is False
        original = manifest.parent / row["original_audio"]
        converted = manifest.parent / row["reference_audio"]
        assert sha256(original) == row["downloaded_source_sha256"]
        assert sha256(converted) == row["reference_validation"]["sha256"]
        assert sf.info(converted).channels == 1
        assert sf.info(converted).subtype == "PCM_16"
    assert sha256(public) == public_hash


def test_partial_download_does_not_replace_existing_catalog(import_context):
    data, stage = import_context
    manifest = importer.install_samples(data, stage, fetch=synthetic_download)
    before = manifest.read_bytes()
    calls = []
    def interrupted(url, path):
        calls.append(url)
        if len(calls) == 2:
            path.write_bytes(b"partial download")
            raise OSError("network interrupted")
        synthetic_download(url, path)
    with pytest.raises(OSError, match="network interrupted"):
        importer.install_samples(data, stage, fetch=interrupted)
    assert manifest.read_bytes() == before
    assert len(VoiceLibrary(data / "voices").list()) == 9


def test_import_rejects_stage_outside_system_temp(import_context):
    data, stage = import_context
    with pytest.raises(ValueError, match="Temp"):
        importer.install_samples(data, data / "downloads", fetch=synthetic_download)
    assert not data.exists()


def test_repeat_import_does_not_rewrite_valid_audio(import_context, monkeypatch):
    data, stage = import_context
    manifest = importer.install_samples(data, stage, fetch=synthetic_download)
    before = manifest.read_bytes()
    def no_copy(*args, **kwargs):
        pytest.fail("existing verified reference must not be rewritten")
    monkeypatch.setattr(importer.shutil, "copyfile", no_copy)
    importer.install_samples(data, stage, fetch=synthetic_download)
    assert manifest.read_bytes() == before


def test_changed_transcript_does_not_keep_stale_demo(import_context):
    data, stage = import_context
    manifest = importer.install_samples(data, stage, fetch=synthetic_download)
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    row = catalog["voices"][0]
    row.update(transcript="过期原文", demo_audio="previous.wav", inference_validation={"success": True})
    manifest.write_text(json.dumps(catalog), encoding="utf-8")
    importer.install_samples(data, stage, fetch=synthetic_download)
    updated = json.loads(manifest.read_text(encoding="utf-8"))["voices"][0]
    assert updated["demo_audio"] == ""
    assert "inference_validation" not in updated
