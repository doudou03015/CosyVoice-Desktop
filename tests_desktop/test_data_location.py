"""One stable profile is selected independently of the launch host or cwd."""
import json
from pathlib import Path

import pytest

from desktop_app import paths


@pytest.fixture
def installation(tmp_path, monkeypatch):
    paths._clear_data_location_cache()
    directory = tmp_path / "应用 程序"
    directory.mkdir()
    monkeypatch.setattr(paths, "app_root", lambda: directory)
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    monkeypatch.delenv("COSYVOICE_DESKTOP_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "legacy"))
    yield directory
    paths._clear_data_location_cache()


def locator(installation, target):
    path = installation / "app-data-location.json"
    path.write_text(json.dumps({"schema_version": 1, "data_dir": str(target)}, ensure_ascii=False), encoding="utf-8-sig")
    return path


def test_explicit_override_precedes_even_an_invalid_locator_and_creates_directory(installation, tmp_path, monkeypatch):
    (installation / "app-data-location.json").write_text("invalid", encoding="utf-8")
    target = tmp_path / "隔离资料"
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(target))
    assert paths.user_data_dir() == target.resolve()
    assert target.is_dir()
    other = tmp_path / "下一隔离资料"
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", str(other))
    assert paths.user_data_dir() == other.resolve()


@pytest.mark.parametrize("override", ["", " \t "])
def test_blank_override_uses_locator_without_modifying_profile(installation, tmp_path, monkeypatch, override):
    target = tmp_path / "固定 数据"
    target.mkdir()
    marker = target / "voices.json"
    marker.write_bytes(b"existing-user-profile")
    locator(installation, target)
    monkeypatch.setenv("COSYVOICE_DESKTOP_DATA", override)
    assert paths.user_data_dir() == target.resolve()
    assert list(target.iterdir()) == [marker]
    assert marker.read_bytes() == b"existing-user-profile"
    assert not (tmp_path / "legacy").exists()


def test_frozen_locator_is_beside_executable_not_internal_or_working_directory(installation, tmp_path, monkeypatch):
    target = tmp_path / "shared-data"
    target.mkdir()
    executable = tmp_path / "real-install"
    executable.mkdir()
    locator(executable, target)
    locator(installation, tmp_path / "must-not-use-internal")
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(executable / "CosyVoice-Desktop.exe"))
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(installation), raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.user_data_dir() == target.resolve()


def test_source_locator_is_found_at_app_root_regardless_of_working_directory(installation, tmp_path, monkeypatch):
    target = tmp_path / "profile"
    target.mkdir()
    locator(installation, target)
    monkeypatch.chdir(tmp_path)
    assert paths.user_data_dir() == target.resolve()


def test_separate_launch_hosts_use_the_same_pinned_profile(installation, tmp_path, monkeypatch):
    target = tmp_path / "common-user-data"
    target.mkdir()
    marker = target / "voices.json"
    marker.write_text('{"voices":["local-preset","recorded-voice"]}', encoding="utf-8")
    locator(installation, target)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(installation / "CosyVoice-Desktop.exe"))
    observed = []
    for host in ("packaged-host-profile", "explorer-profile"):
        paths._clear_data_location_cache()  # A fresh application process.
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / host))
        directory = paths.user_data_dir()
        observed.append((directory, (directory / "voices.json").read_text(encoding="utf-8")))
        assert not (tmp_path / host).exists()
    assert observed[0] == observed[1]


@pytest.mark.parametrize("contents", [
    "broken json", "[]", "{}",
    '{"schema_version": 2, "data_dir": "unused"}',
    '{"schema_version": true, "data_dir": "unused"}',
    '{"schema_version": 1.0, "data_dir": "unused"}',
    '{"schema_version": 1, "data_dir": null}',
    '{"schema_version": 1, "data_dir": ""}',
    '{"schema_version": 1, "data_dir": "  "}',
    '{"schema_version": 1, "data_dir": "relative/profile"}',
])
def test_invalid_locator_never_falls_back_to_empty_legacy_profile(installation, tmp_path, contents):
    (installation / "app-data-location.json").write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="用户数据位置配置无效"):
        paths.user_data_dir()
    assert not (tmp_path / "legacy").exists()


def test_missing_configured_profile_is_not_recreated(installation, tmp_path):
    target = tmp_path / "disconnected-profile"
    locator(installation, target)
    with pytest.raises(ValueError, match="用户数据目录不存在"):
        paths.user_data_dir()
    assert not target.exists()
    assert not (tmp_path / "legacy").exists()


def test_locator_target_cannot_be_a_file(installation, tmp_path):
    target = tmp_path / "not-a-directory"
    target.write_text("preserve", encoding="utf-8")
    locator(installation, target)
    with pytest.raises(ValueError, match="不是文件夹"):
        paths.user_data_dir()
    assert target.read_text(encoding="utf-8") == "preserve"


def test_unreadable_profile_is_reported_without_fallback(installation, tmp_path, monkeypatch):
    target = tmp_path / "protected-profile"
    target.mkdir()
    locator(installation, target)
    def denied(path):
        raise PermissionError("access denied")
    monkeypatch.setattr(paths.os, "scandir", denied)
    with pytest.raises(ValueError, match="无法访问用户数据目录"):
        paths.user_data_dir()
    assert not (tmp_path / "legacy").exists()


def test_profile_without_write_access_is_reported_without_probe_files(installation, tmp_path, monkeypatch):
    target = tmp_path / "read-only-profile"
    target.mkdir()
    locator(installation, target)
    monkeypatch.setattr(paths.os, "access", lambda *args: False)
    with pytest.raises(ValueError, match="读写权限"):
        paths.user_data_dir()
    assert not list(target.iterdir())


def test_a_directory_named_like_locator_is_not_treated_as_absent(installation, tmp_path):
    (installation / "app-data-location.json").mkdir()
    with pytest.raises(ValueError, match="配置无效"):
        paths.user_data_dir()
    assert not (tmp_path / "legacy").exists()


def test_locator_cannot_hot_switch_a_running_profile(installation, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    path = locator(installation, first)
    assert paths.user_data_dir() == first.resolve()
    locator(installation, second)
    assert paths.user_data_dir() == first.resolve()
    path.unlink()
    assert paths.user_data_dir() == first.resolve()
    first.rmdir()
    with pytest.raises(ValueError, match="用户数据目录不存在"):
        paths.user_data_dir()
    assert not first.exists()
    locator(installation, second)
    paths._clear_data_location_cache()
    assert paths.user_data_dir() == second.resolve()


def test_legacy_location_is_created_and_pinned_until_restart(installation, tmp_path):
    legacy = tmp_path / "legacy" / "CosyVoice-Desktop"
    assert paths.user_data_dir() == legacy.resolve()
    target = tmp_path / "new-location"
    target.mkdir()
    locator(installation, target)
    assert paths.user_data_dir() == legacy.resolve()
    paths._clear_data_location_cache()
    assert paths.user_data_dir() == target.resolve()


def test_unreadable_locator_is_not_treated_as_missing(installation, tmp_path, monkeypatch):
    target = tmp_path / "profile"
    target.mkdir()
    location = locator(installation, target)
    native_read = Path.read_text
    def read(path, *args, **kwargs):
        if path == location:
            raise PermissionError("access denied")
        return native_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="配置无效"):
        paths.user_data_dir()
    assert not (tmp_path / "legacy").exists()
