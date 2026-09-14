"""Directory identities can differ from Windows AppData path spellings."""
import os
import json
from pathlib import Path

import pytest

from desktop_app.paths import relative_to_directory
from desktop_app import projects
from desktop_app.voices import VoiceLibrary
from tests_desktop.test_voices import tone


@pytest.fixture
def alias_directory(tmp_path, monkeypatch):
    # Use the backing Temp location before adding our own alias. This prevents
    # the host's real AppData virtualization from adding a second redirection.
    marker = tmp_path / "location.txt"
    marker.write_text("test location", encoding="utf-8")
    backing = marker.resolve().parent
    physical = backing / "physical"
    alias = backing / "logical"
    physical.mkdir()
    try:
        alias.symlink_to(physical, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink unavailable")
    native_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        # Reproduce Windows virtualization: directories keep their logical name,
        # while individual files expose their underlying storage location.
        if path.is_dir() and (path == alias or alias in path.parents):
            return path
        return native_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    return alias, physical


def test_alias_is_accepted_only_when_directory_identity_matches(alias_directory, tmp_path):
    alias, physical = alias_directory
    (physical / "inside.wav").write_bytes(b"reference")
    assert os.path.samefile(alias, physical)
    assert relative_to_directory(alias / "inside.wav", alias) == Path("inside.wav")
    unrelated = tmp_path / "outside.wav"
    unrelated.write_bytes(b"private")
    with pytest.raises(ValueError, match="越界"):
        relative_to_directory(unrelated, alias)
    (physical / "escape.wav").symlink_to(unrelated)
    with pytest.raises(ValueError, match="越界"):
        relative_to_directory(alias / "escape.wav", alias)


def test_custom_voice_save_reload_and_delete_through_alias(alias_directory):
    alias, physical = alias_directory
    source = tone(physical.parent / "recording.wav")
    library = VoiceLibrary(alias)
    voice = library.add("我的录音", source, "测试原文")
    assert Path(voice["audio"]).parent == physical
    assert VoiceLibrary(alias).get(voice["id"])["id"] == voice["id"]
    library.delete(voice["id"])
    assert not Path(voice["audio"]).exists()
    assert source.exists()


def test_local_preset_alias_retains_hidden_state(alias_directory):
    alias, physical = alias_directory
    library = VoiceLibrary(alias)
    (physical / "local-presets").mkdir()
    tone(physical / "local-presets/reference.wav")
    row = dict(id="local-alias", name="本机预设", transcript="原文", reference_audio="reference.wav")
    (physical / "local-presets/manifest.json").write_text(
        json.dumps(dict(schema_version=1, voices=[row])), encoding="utf-8")
    assert library.list()[0]["id"] == "local-alias"
    library.delete("local-alias")
    assert VoiceLibrary(alias).hidden_presets()[0]["id"] == "local-alias"
    library.restore_presets(["local-alias"])
    assert library.list()[0]["id"] == "local-alias"


def test_project_snapshot_alias_keeps_portable_relative_resource(alias_directory):
    alias, physical = alias_directory
    source = tone(physical.parent / "source.wav")
    relative = projects._snapshot(source, alias, "resources/voice.wav")
    assert relative == "resources/voice.wav"
    assert Path(projects.resolve_resource(alias, relative)).is_file()
    assert projects._snapshot(relative, alias, relative) == relative
    with pytest.raises(ValueError, match="越界"):
        projects.resolve_resource(alias, "../outside.wav")
