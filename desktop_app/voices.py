"""Reference voices, with portable metadata and strict audio validation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .documents import sha256
from .paths import app_root, atomic_json, outside_sync, session_dir, user_data_dir


def validate_reference(audio, transcript):
    import numpy as np
    import soundfile as sf
    if not str(transcript).strip():
        raise ValueError("请填写与参考录音一致的原文。")
    path = Path(audio).resolve()
    try:
        info = sf.info(path)
        if info.samplerate < 16000:
            raise ValueError("参考录音采样率必须至少为 16000 Hz。")
        duration = info.frames / info.samplerate
        if not 0 < duration <= 30:
            raise ValueError("参考录音须大于 0 秒且不超过 30 秒。")
        data, rate = sf.read(path, dtype="float32", always_2d=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("无法读取参考录音，请使用有效的 WAV、FLAC 或 MP3。") from error
    if not np.isfinite(data).all():
        raise ValueError("参考录音含无效音频数据。")
    # Validate the mono signal actually consumed by the engine, including anti-phase stereo.
    mono = data.mean(axis=1)
    rms = float(np.sqrt(np.mean(mono.astype("float64") ** 2)))
    if rms < 1e-6:
        raise ValueError("参考录音为静音或合并声道后接近静音，请换一段清晰录音。")
    return dict(sample_rate=rate, channels=info.channels, frames=info.frames,
                duration=duration, rms=rms, sha256=sha256(path))


def temporary_voice(audio, transcript):
    stats = validate_reference(audio, transcript)
    return dict(id="temporary-" + uuid4().hex, name="临时参考录音", audio=str(Path(audio).resolve()),
                transcript=transcript.strip(), kind="temporary", source="本次导入", license="",
                demo_audio="", validation=stats)


class VoiceLibrary:
    def __init__(self, root=None):
        self.root = outside_sync(root or user_data_dir() / "voices")
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest = self.root / "voices.json"
        self.preferences = self.root / "preset-preferences.json"
        self.local_presets = self.root / "local-presets"

    def _custom(self):
        if self.manifest.exists():
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1:
                raise ValueError("音色库版本不受支持。")
            return value.get("voices", [])
        return []

    def _save(self, voices):
        atomic_json(self.manifest, {"schema_version": 1, "voices": voices})

    def _hidden_ids(self):
        if not self.preferences.is_file():
            return set()
        value = json.loads(self.preferences.read_text(encoding="utf-8-sig"))
        if value.get("schema_version") != 1:
            raise ValueError("预设显示设置版本不受支持。")
        ids = value.get("hidden_preset_ids", [])
        if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
            raise ValueError("预设显示设置格式不正确。")
        return set(ids)

    def _save_hidden(self, ids):
        atomic_json(self.preferences, {"schema_version": 1, "hidden_preset_ids": sorted(ids)})

    def presets(self, include_hidden=True):
        """Local samples come first; stable IDs survive moving into a release."""
        result = []
        seen = set()
        hidden = set() if include_hidden else self._hidden_ids()
        for base in (self.local_presets, app_root() / "voice_library"):
            base = base.resolve()
            manifest = base / "manifest.json"
            if not manifest.is_file():
                continue
            source = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if source.get("schema_version") != 1:
                raise ValueError("预设音色库版本不受支持。")
            for row in source.get("voices", []):
                voice_id = row["id"]
                if not isinstance(voice_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", voice_id):
                    raise ValueError("预设音色标识不正确。")
                if voice_id in seen:
                    continue
                if voice_id in hidden:
                    continue
                audio = (base / row["reference_audio"]).resolve()
                if base not in audio.parents or not audio.is_file():
                    continue
                seen.add(voice_id)
                demo = (base / row.get("demo_audio", "")).resolve()
                result.append(dict(id=voice_id, name=row["name"], audio=str(audio),
                                   transcript=row["transcript"], kind="preset",
                                   source=row.get("source_page", ""), license=row.get("license", ""),
                                   attribution=row.get("attribution", ""),
                                   license_url=row.get("license_url", ""),
                                   distribution=row.get("distribution", "bundled"),
                                   demo_audio=str(demo) if base in demo.parents and demo.is_file() else ""))
        return result

    def hidden_presets(self):
        hidden = self._hidden_ids()
        return [voice for voice in self.presets() if voice["id"] in hidden]

    def restore_presets(self, voice_ids):
        requested = set(voice_ids)
        known = {voice["id"] for voice in self.presets()}
        if requested - known:
            raise ValueError("待恢复的预设音色不存在。")
        self._save_hidden(self._hidden_ids() - requested)

    def list(self):
        result = self.presets(include_hidden=False)
        for row in self._custom():
            item = dict(row)
            audio = (self.root / row["audio"]).resolve()
            if self.root not in audio.parents:
                raise ValueError("音色库包含不安全的文件路径。")
            item["audio"] = str(audio)
            result.append(item)
        return result

    def get(self, voice_id):
        for voice in self.list():
            if voice["id"] == voice_id:
                return voice
        raise KeyError("未找到所选音色，请重新选择。")

    def add(self, name, audio, transcript, *, source="用户导入"):
        import soundfile as sf
        if not name.strip():
            raise ValueError("请输入音色名称。")
        stats = validate_reference(audio, transcript)
        voice_id = "custom-" + uuid4().hex
        target = self.root / (voice_id + ".wav")
        temporary = outside_sync(session_dir() / (voice_id + ".wav"))
        try:
            data, rate = sf.read(audio, always_2d=True, dtype="float32")
            sf.write(temporary, data.mean(axis=1), rate, subtype="PCM_16")
            shutil.copyfile(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        stats = validate_reference(target, transcript)
        row = dict(id=voice_id, name=name.strip(), audio=target.name, transcript=transcript.strip(),
                   kind="custom", source=source, license="", demo_audio="", validation=stats)
        custom = self._custom()
        custom.append(row)
        self._save(custom)
        return self.get(voice_id)

    def rename(self, voice_id, name):
        if not name.strip():
            raise ValueError("请输入音色名称。")
        rows = self._custom()
        for row in rows:
            if row["id"] == voice_id:
                row["name"] = name.strip()
                self._save(rows)
                return self.get(voice_id)
        raise ValueError("仅自建音色可以改名。")

    def delete(self, voice_id):
        if any(voice["id"] == voice_id for voice in self.presets()):
            self._save_hidden(self._hidden_ids() | {voice_id})
            return
        rows = self._custom()
        selected = next((row for row in rows if row["id"] == voice_id), None)
        if selected is None:
            raise ValueError("未找到要删除的音色。")
        path = (self.root / selected["audio"]).resolve()
        if self.root not in path.parents or not re.fullmatch(r"custom-[a-f0-9]{32}\.wav", path.name):
            raise ValueError("音色文件路径不正确。")
        # Commit metadata first. Saved projects own independent snapshots of this file.
        self._save([row for row in rows if row["id"] != voice_id])
        path.unlink(missing_ok=True)
