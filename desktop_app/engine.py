"""Shared, lazy CUDA synthesis engine used by the desktop worker and optional web demo."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from uuid import uuid4

from .paths import app_root, configure_worker_environment, outside_sync

PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"


class CancelledError(Exception):
    pass


class Engine:
    def __init__(self, model_dir: str | Path, session: str | Path, progress=None):
        self.model_dir = Path(model_dir).expanduser().resolve()
        self.session = configure_worker_environment(session)
        self.progress = progress or (lambda event: None)
        self.model = None
        self.torch = None
        self.runtime_info = {}
        self._lock = threading.Lock()

    def report(self, stage, message, **details):
        self.progress({"stage": stage, "message": message, **details})

    def _prepare_resources(self):
        root = app_root()
        if not (self.model_dir / "cosyvoice3.yaml").is_file():
            raise ValueError("模型目录中没有 cosyvoice3.yaml，请在设置中安装或选择完整的 CosyVoice 3 模型。")
        candidates = [self.model_dir.parent / "wetext", self.model_dir / "wetext",
                      root / "pretrained_models" / "wetext"]
        if os.environ.get("COSYVOICE_WETEXT_SOURCE"):
            candidates.insert(0, Path(os.environ["COSYVOICE_WETEXT_SOURCE"]))
        relative_files = [Path(lang) / "tn" / name for lang in ("zh", "en")
                          for name in ("tagger.fst", "verbalizer.fst")]
        source = next((p for p in candidates if all((p / q).is_file() for q in relative_files)), None)
        if source is None:
            raise ValueError("缺少中英文离线读音资源，请在设置中修复模型组件。")
        # kaldifst cannot open Unicode absolute paths on Windows. A short relative
        # path in the worker's own directory also works when models are on another drive.
        target = self.session / "wetext"
        for relative in relative_files:
            dst = target / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != (source / relative).stat().st_size:
                shutil.copy2(source / relative, dst)
        os.environ["COSYVOICE_WETEXT_DIR"] = str(target)
        os.chdir(self.session)
        for path in (root, root / "third_party" / "Matcha-TTS"):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

    def load(self):
        if self.model is not None:
            return self.runtime_info
        started = time.perf_counter()
        self._prepare_resources()
        self.report("loading", "正在加载显卡计算组件……")
        import torch
        self.torch = torch
        if not torch.cuda.is_available():
            raise RuntimeError("没有检测到可用的 NVIDIA CUDA 设备，请检查所选显卡、驱动和运行组件。")
        props = torch.cuda.get_device_properties(0)
        self.runtime_info = {"torch": torch.__version__, "cuda": torch.version.cuda,
                             "gpu": props.name, "compute_capability": f"{props.major}.{props.minor}",
                             "total_vram_mb": round(props.total_memory / 1024 ** 2),
                             "python": sys.version.split()[0], "model": "Fun-CosyVoice3-0.5B-2512"}
        if props.total_memory < 7.5 * 1024 ** 3:
            raise RuntimeError("当前显卡显存低于首版支持的 8GB 档位，无法启用配音。")
        # A real kernel launch detects unsupported wheel architectures and driver failures.
        self.report("loading", "正在验证显卡计算能力……")
        with torch.inference_mode():
            probe = torch.ones((32, 32), device="cuda", dtype=torch.float32)
            valid = bool(torch.allclose(probe @ probe, probe * 32))
            torch.cuda.synchronize()
            del probe
        if not valid:
            raise RuntimeError("显卡计算自检失败，请修复运行组件后重试。")
        self.report("loading", "正在加载语音模型，首次加载需要一些时间……")
        from cosyvoice.cli.cosyvoice import AutoModel
        model = AutoModel(model_dir=str(self.model_dir), fp16=True, load_trt=False, load_vllm=False)
        if model.frontend.text_frontend != "wetext":
            del model
            torch.cuda.empty_cache()
            raise RuntimeError("离线文字读音资源未成功加载，请修复模型组件。")
        self.model = model
        self.runtime_info["load_seconds"] = round(time.perf_counter() - started, 2)
        self.report("ready", "模型已就绪。", **self.runtime_info)
        return self.runtime_info

    @staticmethod
    def validate(text, ref_audio, ref_text, speed, seed):
        from .voices import validate_reference
        text, ref_text = str(text or "").strip(), str(ref_text or "").strip()
        if not text:
            raise ValueError("请先输入需要朗读的文字。")
        if len(text) > 100000:
            raise ValueError("单次文字超过 10 万字，请分为多个任务。")
        if not ref_text:
            raise ValueError("请填写参考录音的原文。")
        audio = Path(ref_audio).expanduser().resolve()
        validate_reference(audio, ref_text)
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.5 <= speed <= 2:
            raise ValueError("语速需为 0.5～2 之间的数字。")
        if isinstance(seed, bool) or not isinstance(seed, (int, float)) or not math.isfinite(seed) or int(seed) != seed or not 0 <= seed <= 4294967295:
            raise ValueError("随机种子需为 0～4294967295 之间的整数。")
        return text, audio, ref_text

    def synthesize(self, text, ref_audio, ref_text, output_path, speed=1.0, seed=0, cancel=None):
        self.report("validating", "正在检查文字和参考录音……")
        import numpy as np
        import soundfile as sf
        text, reference, transcript = self.validate(text, ref_audio, ref_text, speed, seed)
        cancel = cancel or threading.Event()
        with self._lock:
            if cancel.is_set():
                raise CancelledError("生成已取消。")
            self.load()
            torch = self.torch
            from cosyvoice.utils.common import set_all_random_seed
            if cancel.is_set():
                raise CancelledError("生成已取消。")
            normalized = self.model.frontend.text_normalize(text, split=True, text_frontend=True)
            segments = [s for s in normalized if str(s).strip()]
            if not segments:
                raise ValueError("文字中没有可朗读的内容。")
            if "<|endofprompt|>" not in transcript:
                transcript = PROMPT_PREFIX + transcript
            fingerprint = hashlib.sha256(json.dumps({"text": segments, "ref": hashlib.sha256(reference.read_bytes()).hexdigest(),
                "transcript": transcript, "speed": speed, "seed": seed, "model": str(self.model_dir),
                "runtime": self.runtime_info}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            cache = self.session / "segments" / fingerprint
            cache.mkdir(parents=True, exist_ok=True)
            stage = self.session / ("speech-" + uuid4().hex + ".wav")
            destination = Path(output_path).expanduser().resolve()
            if destination.suffix.lower() != ".wav":
                raise ValueError("合成输出需为 WAV 文件；MP3 可在生成后导出。")
            if destination == reference:
                raise ValueError("输出文件不能覆盖参考录音。")
            total_frames = 0
            started = time.perf_counter()
            try:
                with sf.SoundFile(str(stage), "w", samplerate=self.model.sample_rate, channels=1, subtype="PCM_16") as writer:
                    for index, segment in enumerate(segments):
                        if cancel.is_set():
                            raise CancelledError("生成已取消，完整页面结果已保留。")
                        self.report("synthesizing", f"正在生成第 {index + 1}/{len(segments)} 段……", completed=index, total=len(segments))
                        cached = cache / f"{index:05d}.wav"
                        if cached.is_file():
                            try:
                                waveform, sr = sf.read(str(cached), dtype="float32")
                                valid_cache = (sr == self.model.sample_rate and waveform.ndim == 1
                                               and len(waveform) and np.isfinite(waveform).all()
                                               and np.any(np.abs(waveform) > 1e-7))
                            except (OSError, RuntimeError, ValueError):
                                valid_cache = False
                            if not valid_cache:
                                cached.unlink()
                        if not cached.is_file():
                            set_all_random_seed((int(seed) + index) % 4294967296)
                            chunks = []
                            try:
                                with torch.inference_mode():
                                    for item in self.model.inference_zero_shot(segment, transcript, str(reference),
                                                                               stream=False, speed=float(speed), text_frontend=False):
                                        chunk = item["tts_speech"].detach().float().cpu().reshape(-1).numpy()
                                        if not np.isfinite(chunk).all():
                                            raise RuntimeError("生成的音频包含无效数值，请重试。")
                                        if len(chunk):
                                            chunks.append(chunk)
                                if not chunks:
                                    raise RuntimeError("模型没有生成音频，请检查文字和参考录音。")
                                waveform = np.concatenate(chunks)
                                if not np.any(np.abs(waveform) > 1e-7):
                                    raise RuntimeError("模型生成了静音，请更换文字或参考录音后重试。")
                                sf.write(str(cached), waveform, self.model.sample_rate, subtype="PCM_16")
                            finally:
                                chunks.clear()
                                torch.cuda.empty_cache()
                        writer.write(waveform)
                        total_frames += len(waveform)
                        self.report("synthesizing", f"第 {index + 1}/{len(segments)} 段已完成。", completed=index + 1, total=len(segments))
                if cancel.is_set():
                    raise CancelledError("生成已取消，完整页面结果已保留。")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(stage, destination)
                return {"path": str(destination), "duration": total_frames / self.model.sample_rate,
                        "sample_rate": self.model.sample_rate, "segments": len(segments),
                        "elapsed": round(time.perf_counter() - started, 2), "runtime": self.runtime_info}
            finally:
                stage.unlink(missing_ok=True)
                torch.cuda.empty_cache()

    def close(self):
        self.model = None
        if self.torch is not None and self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()
