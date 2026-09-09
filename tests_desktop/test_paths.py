import json
from desktop_app.paths import _recovery_file, atomic_json, read_json


def test_interrupted_manifest_copy_recovers_previous_complete_version(tmp_path):
    target = tmp_path / "中文 工程" / "project.json"
    target.parent.mkdir()
    backup = _recovery_file(target)
    previous = {"schema_version": 1, "slides": [{"number": 1, "audio": "audio/slide_0001.wav"}]}
    try:
        backup.write_text(json.dumps(previous), encoding="utf-8")
        target.write_text('{"schema_version":', encoding="utf-8")
        assert read_json(target) == previous
        assert json.loads(target.read_text(encoding="utf-8")) == previous
        assert not backup.exists()
    finally:
        backup.unlink(missing_ok=True)


def test_complete_manifest_replaces_and_leaves_no_adjacent_temporary_file(tmp_path):
    target = tmp_path / "project.json"
    atomic_json(target, {"revision": 1})
    atomic_json(target, {"revision": 2})
    assert read_json(target) == {"revision": 2}
    assert list(tmp_path.iterdir()) == [target]
