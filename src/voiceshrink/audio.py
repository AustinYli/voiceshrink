"""Local PCM WAV I/O and deterministic acoustic mutations."""

from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Mutation


@dataclass
class Audio:
    samples: np.ndarray  # shape: frames, channels; float32 in [-1, 1]
    rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate


def read_wav(path: Path) -> Audio:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
                raise ValueError("VoiceShrink currently requires uncompressed 16-bit PCM WAV audio")
            channels, rate, frames = wav.getnchannels(), wav.getframerate(), wav.getnframes()
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"Invalid WAV file: {path}") from exc
    if not 1 <= channels <= 2 or rate <= 0:
        raise ValueError("WAV must be mono or stereo with a valid sample rate")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels) / 32768.0
    if not len(samples):
        raise ValueError("WAV contains no audio frames")
    return Audio(samples, rate)


def write_wav(path: Path, audio: Audio) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.asarray(audio.samples)
    if audio.rate <= 0 or samples.ndim != 2 or samples.shape[1] not in (1, 2) or not np.isfinite(samples).all():
        raise ValueError("Invalid audio samples")
    pcm = np.rint(np.clip(samples, -1, 32767 / 32768) * 32768).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(samples.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(audio.rate)
        wav.writeframes(pcm.tobytes())


def _bounds(audio: Audio, params: dict) -> tuple[int, int]:
    start = max(0, min(len(audio.samples), round(float(params.get("start", 0)) * audio.rate)))
    end = max(start, min(len(audio.samples), round(float(params.get("end", audio.duration)) * audio.rate)))
    return start, end


def apply_mutation(audio: Audio, mutation: Mutation) -> Audio:
    kind, params = mutation.kind, mutation.parameters
    samples = audio.samples.copy()
    start, end = _bounds(audio, params)
    if kind == "silence":
        position = max(0, min(len(samples), round(float(params.get("position", audio.duration / 2)) * audio.rate)))
        duration = max(0, round(float(params["duration_ms"]) * audio.rate / 1000))
        samples = np.concatenate((samples[:position], np.zeros((duration, samples.shape[1]), dtype=np.float32), samples[position:]))
    elif kind == "noise":
        if end > start:
            signal_rms = float(np.sqrt(np.mean(samples[start:end] ** 2)))
            snr = float(params["snr_db"])
            if math.isfinite(snr):
                rng = np.random.default_rng(int(params.get("seed", 0)))
                noise = rng.standard_normal(samples[start:end].shape).astype(np.float32)
                samples[start:end] += noise * (signal_rms / (10 ** (snr / 20)))
    elif kind == "gain":
        samples[start:end] *= 10 ** (float(params["db"]) / 20)
    elif kind == "dropout":
        if "duration_ms" in params:
            end = min(len(samples), start + round(float(params["duration_ms"]) * audio.rate / 1000))
        samples[start:end] *= 10 ** (float(params.get("attenuation_db", -80)) / 20)
    elif kind == "clipping":
        threshold = float(params["threshold"])
        if not 0 < threshold <= 1:
            raise ValueError("Clipping threshold must be in (0, 1]")
        samples[start:end] = np.clip(samples[start:end], -threshold, threshold)
    elif kind == "speed":
        rate = float(params["rate"])
        if rate <= 0:
            raise ValueError("Speed rate must be positive")
        region = samples[start:end]
        if len(region) > 1 and rate != 1:
            old_x = np.arange(len(region))
            new_x = np.linspace(0, len(region) - 1, max(1, round(len(region) / rate)))
            changed = np.stack([np.interp(new_x, old_x, region[:, c]) for c in range(region.shape[1])], axis=1)
            samples = np.concatenate((samples[:start], changed.astype(np.float32), samples[end:]))
    elif kind == "bandwidth":
        cutoff = float(params["cutoff_hz"])
        if not 0 < cutoff < audio.rate / 2:
            raise ValueError("Bandwidth cutoff must be below Nyquist")
        region = samples[start:end]
        if len(region) > 1:
            alpha = 1 - math.exp(-2 * math.pi * cutoff / audio.rate)
            filtered = np.empty_like(region)
            filtered[0] = region[0]
            for index in range(1, len(region)):
                filtered[index] = filtered[index - 1] + alpha * (region[index] - filtered[index - 1])
            samples[start:end] = filtered
    elif kind == "quantize":
        bits = int(params["bits"])
        if not 2 <= bits <= 16:
            raise ValueError("Quantization bits must be 2..16")
        levels = 2 ** (bits - 1)
        samples[start:end] = np.rint(samples[start:end] * levels) / levels
    elif kind == "resample":
        target_rate = int(params["target_rate"])
        if not 1000 <= target_rate <= audio.rate:
            raise ValueError("Resample target_rate must be between 1000 and source sample rate")
        region = samples[start:end]
        if len(region) > 1 and target_rate != audio.rate:
            # Downsample and return to the original WAV rate so target contracts stay fixed.
            down_count = max(2, round(len(region) * target_rate / audio.rate))
            old_x = np.arange(len(region))
            down_x = np.linspace(0, len(region) - 1, down_count)
            down = np.stack([np.interp(down_x, old_x, region[:, c]) for c in range(region.shape[1])], axis=1)
            samples[start:end] = np.stack([
                np.interp(old_x, down_x, down[:, c]) for c in range(region.shape[1])], axis=1)
    elif kind == "codec":
        codec = str(params.get("codec", "mulaw"))
        if codec != "mulaw":
            raise ValueError("Supported codec simulation: mulaw")
        region = samples[start:end]
        mu = 255.0
        compressed = np.sign(region) * np.log1p(mu * np.abs(region)) / math.log1p(mu)
        quantized = np.rint((compressed + 1) * 127.5) / 127.5 - 1
        samples[start:end] = np.sign(quantized) * np.expm1(np.abs(quantized) * math.log1p(mu)) / mu
    elif kind == "crop":
        if end <= start:
            raise ValueError("Crop must retain a non-empty interval")
        samples = samples[start:end]
    else:
        raise ValueError(f"Unknown mutation: {kind}")
    if not np.isfinite(samples).all():
        raise ValueError("Mutation produced NaN or infinity")
    return Audio(np.clip(samples, -1, 32767 / 32768).astype(np.float32), audio.rate)


def render(audio: Audio, mutations: list[Mutation]) -> Audio:
    for mutation in mutations:
        audio = apply_mutation(audio, mutation)
    return audio
