"""Frame-accurate static-slide video and continuous narration audio."""
from __future__ import annotations

import math
from pathlib import Path
import queue
import shutil
import subprocess
import threading
from uuid import uuid4
import wave

from .paths import outside_sync, session_dir
from .projects import resolve_resource


def _resource(directory, value):
    # API accepts resolved render output or project-relative snapshot resources.
    path = Path(value)
    return path.resolve() if path.is_absolute() else Path(resolve_resource(directory, value))


def build_timeline(slides, project_dir, tail_silence=0.5, fps=30, sample_rate=24000):
    import soundfile as sf
    if fps <= 0 or sample_rate % fps:
        raise ValueError("采样率必须可整除视频帧率。")
    if not math.isfinite(float(tail_silence)) or not 0 <= tail_silence <= 30:
        raise ValueError("页尾停顿须在 0 到 30 秒之间。")
    per_frame = sample_rate // fps
    timeline, start = [], 0
    for slide in slides:
        if not slide.get("included", not slide.get("hidden", False)):
            continue
        number = slide["number"]
        if not slide.get("image"):
            raise ValueError(f"第 {number} 页尚未渲染。")
        image = _resource(project_dir, slide["image"])
        if not image.is_file():
            raise FileNotFoundError(f"第 {number} 页图像不存在，请重新渲染。")
        audio, speech_samples = "", 0
        if slide.get("text", "").strip():
            if not slide.get("audio"):
                raise ValueError(f"第 {number} 页尚未生成配音。")
            audio = str(_resource(project_dir, slide["audio"]))
            info = sf.info(audio)
            if info.samplerate != sample_rate or info.frames <= 0:
                raise ValueError(f"第 {number} 页需要有效的 {sample_rate} Hz 配音，请重新生成。")
            speech_samples = info.frames
            desired_samples = speech_samples + round(tail_silence * sample_rate)
        else:
            blank = float(slide.get("blank_seconds", 3.0))
            if not math.isfinite(blank) or not 0.1 <= blank <= 3600:
                raise ValueError(f"第 {number} 页静音停留须在 0.1 到 3600 秒之间。")
            desired_samples = round(blank * sample_rate)
        frames = (desired_samples + per_frame - 1) // per_frame
        timeline.append(dict(number=number, image=str(image), audio=audio, speech_samples=speech_samples,
                             start_frame=start, frames=frames, start_sample=start * per_frame,
                             samples=frames * per_frame, duration=frames / fps))
        start += frames
    if not timeline:
        raise ValueError("没有选择可导出的视频页面。")
    return timeline


def _continuous_audio(timeline, target, sample_rate=24000, cancel=None):
    import numpy as np
    import soundfile as sf
    with wave.open(str(target), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for page in timeline:
            if cancel and cancel():
                raise InterruptedError("已取消视频合成。")
            written = 0
            if page["audio"]:
                with sf.SoundFile(page["audio"]) as source:
                    for block in source.blocks(blocksize=65536, dtype="float32", always_2d=True):
                        if cancel and cancel():
                            raise InterruptedError("已取消视频合成。")
                        if not np.isfinite(block).all():
                            raise ValueError(f"第 {page['number']} 页音频含无效数值。")
                        mono = np.clip(block.mean(axis=1), -1.0, 32767 / 32768)
                        output.writeframesraw(np.rint(mono * 32768).astype("<i2").tobytes())
                        written += len(mono)
                if written != page["speech_samples"]:
                    raise RuntimeError("配音文件在导出过程中发生变化，请重试。")
            remaining = page["samples"] - written
            while remaining > 0:
                chunk = min(65536, remaining)
                output.writeframesraw(b"\0\0" * chunk)
                remaining -= chunk


def _run_ffmpeg(args, duration, progress=None, cancel=None, writer=None):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(args, stdin=subprocess.PIPE if writer else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=flags)
    lines, errors, write_errors = queue.Queue(), [], []
    def reader(stream, output):
        for line in iter(stream.readline, b""):
            if output:
                lines.put(line.decode("utf-8", "replace").strip())
            else:
                errors.append(line.decode("utf-8", "replace"))
                if len(errors) > 100:
                    del errors[:20]
    def feed():
        try:
            writer(process.stdin)
        except InterruptedError as error:
            write_errors.append(error)
            if process.poll() is None:
                process.kill()
        except (BrokenPipeError, OSError):
            pass  # Encoder failure is reported with its own stderr below.
        except Exception as error:
            write_errors.append(error)
            if process.poll() is None:
                process.kill()
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
    threads = [threading.Thread(target=reader, args=(process.stdout, True), daemon=True),
               threading.Thread(target=reader, args=(process.stderr, False), daemon=True)]
    if writer:
        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    try:
        while process.poll() is None or not lines.empty():
            if cancel and cancel():
                raise InterruptedError("已取消导出。")
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line.startswith("out_time_us=") and progress:
                try:
                    done = min(duration, max(0, int(line.split("=", 1)[1]) / 1e6))
                    progress(dict(stage="encode", completed=done, total=duration,
                                  message=f"正在编码：{done:.1f} / {duration:.1f} 秒"))
                except ValueError:
                    pass
        for thread in threads:
            thread.join(timeout=5)
        if write_errors:
            raise write_errors[0]
        if process.wait() != 0:
            raise RuntimeError("FFmpeg 编码失败：" + "".join(errors)[-1800:])
        if progress:
            progress(dict(stage="encode", completed=duration, total=duration, message="编码完成"))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


def export_audio(source, destination, ffmpeg="ffmpeg", progress=None, cancel=None):
    import soundfile as sf
    destination = Path(destination).resolve()
    suffix = destination.suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a"}:
        raise ValueError("音频导出格式须为 WAV、MP3 或 M4A。")
    temporary = outside_sync(session_dir() / ("audio-" + uuid4().hex + suffix))
    try:
        info = sf.info(source)
        if suffix == ".wav":
            codec = ["-c:a", "pcm_s16le"]
        elif suffix == ".mp3":
            codec = ["-c:a", "libmp3lame", "-b:a", "192k"]
        else:
            codec = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
        args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-vn", *codec, "-progress", "pipe:1", "-nostats", str(temporary)]
        _run_ffmpeg(args, info.duration, progress, cancel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(temporary, destination)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def export_video(slides, project_dir, destination, ffmpeg="ffmpeg", gpu_index=0,
                 tail_silence=0.5, progress=None, cancel=None):
    from PIL import Image, ImageOps
    destination = Path(destination).resolve()
    if destination.suffix.lower() != ".mp4":
        raise ValueError("视频输出请选择 MP4 文件。")
    timeline = build_timeline(slides, project_dir, tail_silence)
    frames = sum(page["frames"] for page in timeline)
    duration = frames / 30
    workspace = outside_sync(session_dir() / ("video-" + uuid4().hex))
    workspace.mkdir()
    try:
        audio = workspace / "narration.wav"
        output = workspace / "result.mp4"
        _continuous_audio(timeline, audio, cancel=cancel)
        def write_frames(stream):
            for page in timeline:
                if cancel and cancel():
                    raise InterruptedError("已取消视频合成。")
                with Image.open(page["image"]) as image:
                    frame = ImageOps.pad(image.convert("RGB"), (1920, 1080),
                                         method=Image.Resampling.LANCZOS, color="black").tobytes()
                for _ in range(page["frames"]):
                    if cancel and cancel():
                        raise InterruptedError("已取消视频合成。")
                    stream.write(frame)
        failures = []
        for encoder in ("h264_nvenc", "h264_mf"):
            codec = ["-c:v", encoder, "-b:v", "6M"]
            if encoder == "h264_nvenc":
                codec += ["-gpu", str(int(gpu_index)), "-preset", "p4"]
            else:
                codec += ["-hw_encoding", "0"]
            args = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "1920x1080",
                    "-framerate", "30", "-i", "pipe:0", "-i", str(audio),
                    "-map", "0:v:0", "-map", "1:a:0", *codec, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "24000", "-movflags", "+faststart",
                    "-video_track_timescale", "30000", "-movie_timescale", "30000",
                    "-progress", "pipe:1", "-nostats", str(output)]
            try:
                if progress:
                    progress(dict(stage="encode", completed=0, total=duration,
                                  message="正在使用 " + encoder + " 编码视频"))
                _run_ffmpeg(args, duration, progress, cancel, write_frames)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(output, destination)
                return dict(path=str(destination), duration=duration, frames=frames,
                            encoder=encoder, timeline=timeline)
            except RuntimeError as error:
                failures.append(str(error))
        raise RuntimeError("NVIDIA 和 Windows H.264 编码均不可用。\n" + "\n".join(failures))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
