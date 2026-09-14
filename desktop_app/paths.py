"""Portable application paths. No model libraries are imported here."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

_SESSION: Path | None = None
_DATA_LOCATION_CACHE: dict[tuple[str, str], tuple[Path, bool]] = {}


def outside_sync(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    forbidden = Path(r"E:\BaiduSyncdisk").resolve()
    if result == forbidden or forbidden in result.parents:
        raise ValueError("运行组件、缓存和临时文件需保存在百度同步盘之外。")
    return result


def relative_to_directory(path: str | Path, directory: str | Path) -> Path:
    """Containment by directory identity, including Windows AppData aliases.

    Packaged Windows applications can resolve a file to LocalCache while its
    parent still resolves to AppData. Only an ancestor with the same filesystem
    identity as the requested directory is accepted; unrelated paths and
    symlinks escaping that directory remain rejected.
    """
    result, root = Path(path).resolve(), Path(directory).resolve()
    try:
        return result.relative_to(root)
    except ValueError:
        for ancestor in (result, *result.parents):
            try:
                if ancestor.samefile(root):
                    return result.relative_to(ancestor)
            except OSError:
                continue
    raise ValueError("资源路径越界。")


def app_root() -> Path:
    override = os.environ.get("COSYVOICE_DESKTOP_ROOT")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent.parent


def _clear_data_location_cache() -> None:
    """Reset startup location decisions for isolated tests; not a live UI switch."""
    _DATA_LOCATION_CACHE.clear()


def _usable_data_directory(directory: Path, create: bool) -> Path:
    try:
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise ValueError(f"用户数据目录不存在或不是文件夹：{directory}。请恢复该目录或修正 app-data-location.json。")
        if not os.access(directory, os.R_OK | os.W_OK):
            raise PermissionError("目录没有读写权限")
        # Opening the iterator checks directory access without creating probe
        # files in a user's profile or enumerating its contents.
        with os.scandir(directory):
            pass
    except OSError as error:
        raise ValueError(f"无法访问用户数据目录：{directory}。请检查磁盘连接和目录读写权限。") from error
    return directory


def _locator_directory(installation: Path) -> Path | None:
    locator = installation / "app-data-location.json"
    try:
        locator.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"无法读取用户数据位置配置：{locator}。请检查文件权限。") from error
    try:
        if not locator.is_file():
            raise ValueError("配置必须是可读取的 JSON 文件")
        value = json.loads(locator.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise ValueError("schema_version 必须为 1")
        base = value.get("data_dir")
        if not isinstance(base, str) or not base.strip() or not Path(base.strip()).is_absolute():
            raise ValueError("data_dir 必须是非空的绝对文件夹路径")
        return outside_sync(base.strip())
    except (OSError, ValueError) as error:
        raise ValueError(f"用户数据位置配置无效：{locator}。{error}。未切换到其他资料库。") from error


def user_data_dir() -> Path:
    override = os.environ.get("COSYVOICE_DESKTOP_DATA", "").strip()
    if override:
        # Explicit overrides remain independently selectable for development
        # and isolated tests. A running application's environment is unchanged.
        key = ("environment", override)
        chosen = _DATA_LOCATION_CACHE.get(key)
        if chosen is None:
            chosen = (outside_sync(override), True)
    else:
        installation = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else app_root()
        key = ("installation", str(installation))
        chosen = _DATA_LOCATION_CACHE.get(key)
        if chosen is None:
            directory = _locator_directory(installation)
            if directory is None:
                base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "CosyVoice-Desktop"
                chosen = (outside_sync(base), True)
            else:
                # A locator pins an existing profile. A missing drive/profile
                # must never create an apparently empty replacement library.
                chosen = (directory, False)
    result = _usable_data_directory(*chosen)
    _DATA_LOCATION_CACHE[key] = chosen
    return result


def session_dir() -> Path:
    global _SESSION
    if _SESSION is None:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Temp"
        else:
            base = Path(tempfile.gettempdir())
        base = outside_sync(base)
        base.mkdir(parents=True, exist_ok=True)
        _SESSION = outside_sync(Path(tempfile.mkdtemp(prefix="cosyvoice-desktop-", dir=str(base))))
    return _SESSION


def configure_worker_environment(session: str | Path | None = None) -> Path:
    folder = outside_sync(session or session_dir())
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = str(folder)
    tempfile.tempdir = str(folder)
    for name, child in {
        "HF_HOME": "huggingface", "HF_HUB_CACHE": "huggingface/hub",
        "HUGGINGFACE_HUB_CACHE": "huggingface/hub", "MODELSCOPE_CACHE": "modelscope",
        "TORCH_HOME": "torch", "NUMBA_CACHE_DIR": "numba", "MPLCONFIGDIR": "matplotlib",
        "XDG_CACHE_HOME": "cache", "GRADIO_TEMP_DIR": "gradio",
    }.items():
        cache = folder / child
        cache.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(cache)
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                       "HF_HUB_DISABLE_TELEMETRY": "1", "GRADIO_ANALYTICS_ENABLED": "False",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    os.environ.pop("PYTHONPYCACHEPREFIX", None)
    sys.dont_write_bytecode = True
    sys.pycache_prefix = None
    return folder


def atomic_json(path: Path, value: object) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = session_dir() / ("manifest-" + uuid4().hex + ".json")
    backup = _recovery_file(path)
    preserve_backup = False
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.drive.casefold() == temp.drive.casefold():
            os.replace(temp, path)
        else:
            # Windows cannot atomically rename across drives. Keep a recovery
            # copy in this session until the complete destination is flushed.
            existed = path.is_file()
            if existed:
                shutil.copyfile(path, backup)
                preserve_backup = True
            try:
                with temp.open("rb") as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
                preserve_backup = False
            except BaseException:
                if existed:
                    shutil.copyfile(backup, path)
                else:
                    path.unlink(missing_ok=True)
                preserve_backup = False
                raise
    finally:
        temp.unlink(missing_ok=True)
        if not preserve_backup:
            backup.unlink(missing_ok=True)


def _recovery_file(path: Path) -> Path:
    key = hashlib.sha256(str(Path(path).resolve()).casefold().encode("utf-8")).hexdigest()
    folder = outside_sync(session_dir().parent / "CosyVoice-Desktop-recovery")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (key + ".json")


def read_json(path: Path):
    """Recover a manifest interrupted during a Windows cross-drive final copy."""
    path = Path(path).resolve()
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        backup = _recovery_file(path)
        if not backup.is_file():
            raise
        value = json.loads(backup.read_text(encoding="utf-8-sig"))
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup, path)
        backup.unlink()
        return value


def load_settings() -> dict:
    defaults = {"model_dir": "", "runtime_python": "", "runtime_id": "",
                "gpu_uuid": "", "ffmpeg_path": "", "tail_silence": 0.5,
                "component_dir": str(user_data_dir() / "components"), "manifest_path": ""}
    path = user_data_dir() / "settings.json"
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                defaults.update(value)
        except (OSError, ValueError):
            pass
    return defaults


def save_settings(settings: dict) -> None:
    atomic_json(user_data_dir() / "settings.json", settings)
