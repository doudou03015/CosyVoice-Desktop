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


def outside_sync(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    forbidden = Path(r"E:\BaiduSyncdisk").resolve()
    if result == forbidden or forbidden in result.parents:
        raise ValueError("运行组件、缓存和临时文件需保存在百度同步盘之外。")
    return result


def app_root() -> Path:
    override = os.environ.get("COSYVOICE_DESKTOP_ROOT")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    base = os.environ.get("COSYVOICE_DESKTOP_DATA")
    if not base:
        base = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "CosyVoice-Desktop")
    result = outside_sync(base)
    result.mkdir(parents=True, exist_ok=True)
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
