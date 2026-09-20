"""Explicit local import of user-selected demos; never part of release packaging.

The demo site's academic-use disclaimer does not grant redistribution rights.
This tool stores recordings only in the chosen user's local voice library.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from desktop_app.documents import sha256
from desktop_app.paths import atomic_json, outside_sync
from desktop_app.voices import VoiceLibrary, validate_reference


SOURCE_PAGE = "https://qwenaudio.github.io/cosyvoice3/#Target%20Speaker%20Fine-tune%20Models"
BASE_URL = "https://qwenaudio.github.io/cosyvoice3/audio/c3_large/SFT/"
DEMO_TEXT = "大家好，欢迎收听今天的讲解。接下来，我们会按照页面顺序，介绍主要内容、关键步骤和需要注意的事项。"
SAMPLES = [
    ("cosyvoice_demo_longwan_zh", "示范音色1", "minority_langugae/sft_longwan_zh.wav",
     "我们将为全球城市的可持续发展贡献力量。"),
    ("cosyvoice_demo_longshu_zh", "示范音色2", "minority_langugae/sft_longshu_zh.wav",
     "我们将为全球城市的可持续发展贡献力量。"),
    ("cosyvoice_demo_longcheng_zh", "示范音色3", "00004506-00000103.wav",
     "真不好意思，从小至今，他还从来没有被哪一位异性朋友亲吻过呢。"),
]


def download(url, destination):
    import requests
    written = 0
    with requests.get(url, stream=True, timeout=(20, 60)) as response:
        response.raise_for_status()
        with destination.open("wb") as stream:
            for chunk in response.iter_content(128 * 1024):
                written += len(chunk)
                if written > 16 * 1024 * 1024:
                    raise ValueError("参考录音下载大小超出限制。")
                stream.write(chunk)


def install_verified(source, folder, prefix, digest):
    target = folder / (prefix + "-" + digest[:16] + ".wav")
    if target.is_file() and sha256(target) == digest:
        return target
    # Never truncate a file that a previously committed catalog may reference.
    if target.exists():
        target = folder / (prefix + "-" + digest[:16] + "-" + uuid4().hex + ".wav")
    try:
        shutil.copyfile(source, target)
        if sha256(target) != digest:
            raise ValueError("参考录音复制后校验失败。")
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def install_samples(data_dir, stage, fetch=download):
    import soundfile as sf
    from desktop_app import paths
    stage = outside_sync(stage)
    system_temp = outside_sync(Path(os.environ["LOCALAPPDATA"]) / "Temp")
    if system_temp not in stage.parents:
        raise ValueError("下载与转换目录必须位于系统 Temp 的任务目录中。")
    stage.mkdir(parents=True, exist_ok=True)
    paths._SESSION = stage
    data_dir = outside_sync(data_dir)
    os.environ["COSYVOICE_DESKTOP_DATA"] = str(data_dir)
    # Download and validate all selected files before touching the local catalog.
    prepared = []
    for voice_id, name, resource, transcript in SAMPLES:
        original = stage / (voice_id + "-source.wav")
        fetch(BASE_URL + resource, original)
        validate_reference(original, transcript)
        digest = sha256(original)
        converted = stage / (voice_id + "-reference.wav")
        samples, rate = sf.read(original, dtype="float32", always_2d=True)
        sf.write(converted, samples.mean(axis=1), rate, subtype="PCM_16")
        validation = validate_reference(converted, transcript)
        prepared.append((voice_id, name, resource, transcript, original, converted, digest, validation))
    library = VoiceLibrary(data_dir / "voices")
    catalog_path = library.local_presets / "manifest.json"
    previous = json.loads(catalog_path.read_text(encoding="utf-8-sig")) if catalog_path.exists() else {"schema_version": 1, "voices": []}
    if previous.get("schema_version") != 1:
        raise ValueError("本机预设清单版本不受支持。")
    previous_rows = {row["id"]: row for row in previous.get("voices", [])}
    rows = []
    for voice_id, name, resource, transcript, original, converted, digest, validation in prepared:
        folder = library.local_presets / voice_id
        folder.mkdir(parents=True, exist_ok=True)
        reference = install_verified(converted, folder, "reference", validation["sha256"])
        source = install_verified(original, folder, "source", digest)
        row = dict(id=voice_id, name=name, transcript=transcript, source_page=SOURCE_PAGE,
                   source_audio_url=BASE_URL + resource,
                   attribution="CosyVoice / Tongyi Speech Team official demo",
                   license="官方演示样本 · 再分发许可待确认", license_url=SOURCE_PAGE,
                   distribution="local_only", redistribution_approved=False,
                   original_audio=source.relative_to(library.local_presets).as_posix(),
                   downloaded_source_sha256=digest,
                   reference_audio=reference.relative_to(library.local_presets).as_posix(),
                   reference_validation=validation, demo_text=DEMO_TEXT, demo_audio="",
                   processing="保留完整时长和原采样率；声道平均转换为单声道 PCM_16 WAV；无剪辑、降噪或变速。")
        old = previous_rows.get(voice_id, {})
        if (old.get("reference_validation", {}).get("sha256") == validation["sha256"]
                and old.get("transcript") == transcript and old.get("demo_text") == DEMO_TEXT):
            for key in ("demo_audio", "demo_text", "inference_validation"):
                if key in old:
                    row[key] = old[key]
        rows.append(row)
    selected = {row["id"] for row in rows}
    rows.extend(row for row in previous.get("voices", []) if row["id"] not in selected)
    atomic_json(catalog_path, dict(schema_version=1, distribution="local_only", voices=rows))
    return catalog_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="仅向本机音色库导入已选官方演示录音，不授权公开分发。")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    print(install_samples(args.data_dir, args.stage))
