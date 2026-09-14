"""Conservative, sample-based decisions for joins between generated segments.

This module only examines the outside edge of two *complete* mono segments. It
does not remove pauses within a segment, modify samples, apply fades, or write
files. The caller can retain the original segment cache and slice by the returned
sample counts. Parameters are deliberately configurable for waveform validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Integral

import numpy as np


@dataclass(frozen=True)
class JoinConfig:
    trigger_seconds: float = 0.45
    target_seconds: float = 0.30
    guard_seconds: float = 0.08
    window_seconds: float = 0.01
    silence_rms_dbfs: float = -60.0
    silence_peak_dbfs: float = -50.0
    relative_silence_db: float = -36.0
    min_speech_rms_dbfs: float = -42.0
    min_active_seconds: float = 0.10
    speech_percentile: float = 90.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if not 0 < self.target_seconds < self.trigger_seconds:
            raise ValueError("Require 0 < target_seconds < trigger_seconds")
        if not 0 < self.guard_seconds * 2 <= self.target_seconds:
            raise ValueError("The target must retain both positive edge guards")
        if not 0 < self.window_seconds <= self.guard_seconds:
            raise ValueError("The analysis window must fit within an edge guard")
        if self.min_active_seconds < self.window_seconds:
            raise ValueError("Active speech evidence must span at least one window")
        for name in ("silence_rms_dbfs", "silence_peak_dbfs", "relative_silence_db", "min_speech_rms_dbfs"):
            if getattr(self, name) >= 0:
                raise ValueError(f"{name} must be negative")
        if self.silence_rms_dbfs >= self.min_speech_rms_dbfs:
            raise ValueError("Silence RMS must be lower than the minimum speech RMS")
        if self.silence_peak_dbfs < self.silence_rms_dbfs:
            raise ValueError("Silence peak ceiling must be at least its RMS ceiling")
        if not 50 <= self.speech_percentile < 100:
            raise ValueError("speech_percentile must be in [50, 100)")


@dataclass(frozen=True)
class BoundaryJoin:
    """Counts describe detected quiet edges, not all perceptual speech pauses."""

    sample_rate: int
    left_trim_samples: int
    right_trim_samples: int
    left_edge_samples: int
    right_edge_samples: int
    before_samples: int
    after_samples: int
    changed: bool
    reason: str
    left_speech_rms: float
    right_speech_rms: float
    left_silence_rms: float
    right_silence_rms: float

    def to_dict(self):
        return asdict(self)


def _amplitude(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _waveform(value, side: str) -> np.ndarray:
    data = np.asarray(value)
    if data.ndim != 1 or not len(data):
        raise ValueError(f"{side} must be a non-empty mono waveform")
    if data.dtype.kind != "f":
        raise ValueError(f"{side} must contain floating-point audio samples")
    if not np.isfinite(data).all():
        raise ValueError(f"{side} contains non-finite audio samples")
    if not np.any(np.abs(data) > 1e-7):
        raise ValueError(f"{side} is silent; a silent generated segment cannot be joined")
    return data


def _speech_level(data: np.ndarray, window: int, sample_rate: int, config: JoinConfig):
    # Float64 accumulation avoids underflow without copying a complete long
    # waveform into a second precision. Ignore a short final window for evidence.
    count = len(data) // window
    if not count:
        return 0.0, False
    frames = data[:count * window].reshape(count, window)
    energy = np.einsum("ij,ij->i", frames, frames, dtype=np.float64)
    rms = np.sqrt(energy / window)
    level = float(np.percentile(rms, config.speech_percentile))
    minimum = _amplitude(config.min_speech_rms_dbfs)
    active = int(np.count_nonzero(rms >= minimum)) * window
    confident = level >= minimum and active >= math.ceil(config.min_active_seconds * sample_rate)
    return level, confident


def _quiet_edge(data: np.ndarray, window: int, rms_limit: float, peak_limit: float, trailing: bool) -> int:
    # Scan from the actual edge and stop at the first uncertain window. A quiet
    # consonant/breath above either conservative ceiling blocks further trimming.
    edge = data[::-1] if trailing else data
    quiet = 0
    for start in range(0, len(edge), window):
        frame = edge[start:start + window]
        if float(np.max(np.abs(frame))) > peak_limit:
            break
        energy = float(np.einsum("i,i->", frame, frame, dtype=np.float64))
        if math.sqrt(energy / len(frame)) > rms_limit:
            break
        quiet += len(frame)
    return quiet


def plan_boundary_join(left, right, sample_rate: int, config: JoinConfig | None = None) -> BoundaryJoin:
    """Plan removal of excessive confirmed silence at one segment boundary.

    Slice ``left[:len(left)-result.left_trim_samples]`` and
    ``right[result.right_trim_samples:]`` and concatenate without overlap. Never
    use ``left[:-trim]`` when ``trim`` is zero. Inputs and raw caches are unchanged.

    Both segments must contain sufficient clear speech evidence. All-silent or
    invalid model output raises ValueError; low-level or ambiguous audio passes
    through unchanged. Edge guards are additional retained *quiet* audio before
    the first uncertain window, so no classified speech window is cut.
    """
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral) or sample_rate < 8000:
        raise ValueError("sample_rate must be an integer of at least 8000 Hz")
    sample_rate = int(sample_rate)
    config = JoinConfig() if config is None else config
    if not isinstance(config, JoinConfig):
        raise TypeError("config must be a JoinConfig")
    left, right = _waveform(left, "left"), _waveform(right, "right")
    window = max(1, math.ceil(config.window_seconds * sample_rate))
    levels = [_speech_level(data, window, sample_rate, config) for data in (left, right)]
    relative = _amplitude(config.relative_silence_db)
    thresholds = [min(_amplitude(config.silence_rms_dbfs), level * relative) for level, _ in levels]

    def decision(left_edge=0, right_edge=0, left_trim=0, right_trim=0, reason="low_level"):
        before = left_edge + right_edge
        return BoundaryJoin(sample_rate, left_trim, right_trim, left_edge, right_edge,
                            before, before - left_trim - right_trim, bool(left_trim or right_trim), reason,
                            levels[0][0], levels[1][0], thresholds[0], thresholds[1])

    if not all(confident for _, confident in levels):
        return decision()
    edges = [_quiet_edge(data, window, threshold,
                         min(_amplitude(config.silence_peak_dbfs), threshold * 3.0), trailing)
             for data, threshold, trailing in zip((left, right), thresholds, (True, False))]
    before = sum(edges)
    if before <= round(config.trigger_seconds * sample_rate):
        return decision(*edges, reason="within_limit" if before else "no_clear_edge")

    target = round(config.target_seconds * sample_rate)
    guard = math.ceil(config.guard_seconds * sample_rate)
    # Each side retains its guard, or all its samples if the quiet edge is shorter.
    # Distribute the remaining retention budget proportionally to available quiet
    # material; the cut never reaches an uncertain window, even for one-sided gaps.
    available = [max(0, edge - guard) for edge in edges]
    remove = before - target
    if remove > sum(available):
        return decision(*edges, reason="guard_limit")
    left_trim = min(available[0], round(remove * available[0] / sum(available)))
    right_trim = remove - left_trim
    if right_trim > available[1]:
        right_trim = available[1]
        left_trim = remove - right_trim
    return decision(*edges, left_trim, right_trim, reason="shortened")
