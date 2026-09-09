"""Portable project manifests and content-based audio reuse."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from .documents import read_pptx, sha256
from .paths import atomic_json, read_json

SCHEMA_VERSION = 1


def resolve_resource(directory, relative):
    root = Path(directory).resolve()
    value = Path(relative)
    if value.is_absolute():
        raise ValueError("工程资源必须使用相对路径。")
    result = (root / value).resolve()
    if result != root and root not in result.parents:
        raise ValueError("工程资源路径越界。")
    return str(result)


def _snapshot(source, directory, relative):
    root = Path(directory).resolve()
    candidate = Path(source)
    if not candidate.is_absolute():
        candidate = Path(resolve_resource(root, source))
    candidate = candidate.resolve()
    target = Path(resolve_resource(root, relative))
    if candidate != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, target)
    if not target.is_file():
        raise FileNotFoundError(f"工程资源不存在：{target.name}")
    return target.relative_to(root).as_posix()


def create_project(pptx, directory, voice=None, speed=1.0):
    project = read_pptx(pptx)
    project.update(schema_version=SCHEMA_VERSION, name=Path(pptx).stem, voices={},
                   settings={"voice_id": voice["id"] if voice else "", "speed": speed,
                             "seed": 0, "tail_silence": 0.5},
                   model_id="Fun-CosyVoice3-0.5B-2512", runtime_id="")
    save_project(project, directory, {voice["id"]: voice} if voice else None)
    return project


def save_project(project, directory, voices=None):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    staged = copy.deepcopy(project)
    staged["schema_version"] = SCHEMA_VERSION
    if staged.get("source"):
        staged["source"] = _snapshot(staged["source"], root, "source/presentation.pptx")
    archive = staged.setdefault("voices", {})
    for voice_id, voice in (voices or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice_id):
            raise ValueError("无效的音色标识。")
        row = {key: value for key, value in voice.items() if key not in {"demo_audio", "validation"}}
        row["audio"] = _snapshot(voice["audio"], root, f"resources/voices/{voice_id}.wav")
        archive[voice_id] = row
    for slide in staged["slides"]:
        for field, folder, suffix in (("audio", "audio", ".wav"), ("image", "images", ".png")):
            if slide.get(field):
                slide[field] = _snapshot(slide[field], root,
                                         f"{folder}/slide_{slide['number']:04d}{suffix}")
    _validate(staged, root)
    atomic_json(root / "project.json", staged)
    # Keep slide object identity: UI task queues can retain references while saving.
    for original, saved in zip(project["slides"], staged["slides"]):
        original.clear()
        original.update(saved)
    for key, value in staged.items():
        if key != "slides":
            project[key] = value


def _validate(project, root):
    if project.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("此工程版本暂不受支持，请使用相应版本的软件。")
    if not isinstance(project.get("slides"), list):
        raise ValueError("工程缺少页面清单。")
    numbers = set()
    for slide in project["slides"]:
        number = slide.get("number")
        if not isinstance(number, int) or number < 1 or number in numbers:
            raise ValueError("工程页码无效或重复。")
        numbers.add(number)
        for field in ("audio", "image"):
            if slide.get(field):
                resolve_resource(root, slide[field])
    if project.get("source"):
        resolve_resource(root, project["source"])
    for voice in project.get("voices", {}).values():
        resolve_resource(root, voice["audio"])


def load_project(directory):
    root = Path(directory).resolve()
    if root.name == "project.json":
        root = root.parent
    project = read_json(root / "project.json")
    _validate(project, root)
    for slide in project["slides"]:
        if slide.get("status") in {"generating", "running"}:
            slide["status"] = "pending"
        if slide.get("audio") and not Path(resolve_resource(root, slide["audio"])).exists():
            slide["status"] = "missing"
    return project


def effective_settings(project, slide):
    defaults = project.get("settings", {})
    speed = slide.get("speed")
    if speed is None:
        speed = defaults.get("speed", 1.0)
    if not math.isfinite(float(speed)) or not 0.5 <= float(speed) <= 2.0:
        raise ValueError("语速须在 0.5 到 2.0 之间。")
    return dict(voice_id=slide.get("voice_id") or defaults.get("voice_id", ""),
                speed=float(speed), seed=int(slide.get("seed", defaults.get("seed", 0))))


def fingerprint(text, voice, speed=1.0, seed=0, model_id="", runtime_id=""):
    # Deliberately exclude machine paths and speaker display names.
    payload = dict(text=text.strip(), audio_sha256=sha256(voice["audio"]),
                   ref_text=voice["transcript"].strip(), speed=float(speed), seed=int(seed),
                   model_id=model_id, runtime_id=runtime_id)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def remember_audio(project, slide, directory, audio, fingerprint):
    relative = _snapshot(audio, directory, f"audio/slide_{slide['number']:04d}.wav")
    slide.update(audio=relative, fingerprint=fingerprint, status="complete",
                 audio_sha256=sha256(resolve_resource(directory, relative)))
    save_project(project, directory)


def cached_audio(slide, directory, fingerprint):
    if slide.get("fingerprint") != fingerprint or not slide.get("audio"):
        return None
    path = Path(resolve_resource(directory, slide["audio"]))
    if not path.is_file() or sha256(path) != slide.get("audio_sha256"):
        return None
    return str(path)
