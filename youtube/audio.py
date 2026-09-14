"""Audio as numpy samples: one mono float32 format shared by every voice adapter.

Every adapter returns SAMPLE_RATE mono audio, so clips from different voices,
or different providers, join without resampling.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 24000


@dataclass
class Audio:
    samples: np.ndarray  # mono float32 in [-1, 1]
    sample_rate: int = SAMPLE_RATE

    def __post_init__(self):
        samples = np.asarray(self.samples, dtype=np.float32)
        if samples.ndim != 1:
            raise ValueError("Audio must be mono (a 1-D array of samples)")
        self.samples = samples

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    def to_wav(self) -> bytes:
        pcm = (np.clip(self.samples, -1.0, 1.0) * 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(self.sample_rate)
            out.writeframes(pcm.tobytes())
        return buffer.getvalue()

    @classmethod
    def from_wav(cls, data: bytes) -> "Audio":
        with wave.open(io.BytesIO(data), "rb") as src:
            if src.getsampwidth() != 2:
                raise ValueError("Only 16-bit PCM WAV is supported")
            channels, rate = src.getnchannels(), src.getframerate()
            pcm = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2")
        samples = pcm.astype(np.float32) / 32767
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        return cls(samples=samples, sample_rate=rate)


def silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> Audio:
    return Audio(np.zeros(int(round(seconds * sample_rate)), dtype=np.float32), sample_rate)


def concatenate(clips: list[Audio], gaps: list[float] | None = None) -> Audio:
    """Join clips in order, with gaps[i] seconds of silence after clip i (not after the last)."""
    if not clips:
        raise ValueError("Nothing to concatenate")
    rate = clips[0].sample_rate
    if any(c.sample_rate != rate for c in clips):
        raise ValueError("All clips must share one sample rate")
    gaps = gaps or [0.0] * (len(clips) - 1)
    if len(gaps) != len(clips) - 1:
        raise ValueError("Need one gap between each pair of clips")

    parts = []
    for i, clip in enumerate(clips):
        parts.append(clip.samples)
        if i < len(gaps) and gaps[i] > 0:
            parts.append(silence(gaps[i], rate).samples)
    return Audio(np.concatenate(parts), rate)
