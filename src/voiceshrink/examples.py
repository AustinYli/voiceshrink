"""Deterministic reference targets for a fully local demo."""

from __future__ import annotations

import numpy as np

from .audio import read_wav
from .models import Scenario, StageResult, TargetResult
from .transport import StreamPlan, transport_summary


def _longest_quiet_seconds(path, amplitude: float = 0.003) -> float:
    audio = read_wav(path)
    quiet = np.max(np.abs(audio.samples), axis=1) < amplitude
    longest = current = 0
    for value in quiet:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest / audio.rate


def _longest_low_energy_seconds(audio, threshold: float = 0.02) -> float:
    block = max(1, audio.rate // 100)
    mono = audio.samples.mean(axis=1)
    count = len(mono) // block
    if count == 0:
        return 0.0
    rms = np.sqrt(np.mean(mono[:count * block].reshape(count, block) ** 2, axis=1))
    longest = current = 0
    for low in rms < threshold:
        current = current + 1 if low else 0
        longest = max(longest, current)
    return longest * block / audio.rate


def pause_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted bug: an inserted pause around 500 ms corrupts an order ID."""
    broken = _longest_quiet_seconds(audio_path) >= 0.5
    order_id = "7421" if broken else "74821"
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": order_id}},
                        [StageResult("asr", f"order {order_id}"),
                         StageResult("tool", {"order_id": order_id})])


def pause_intervention(audio_path, scenario: Scenario, stage: str, replacement) -> TargetResult:
    """Reference intervention: replace ASR text, then execute the downstream fake tool."""
    if stage != "asr":
        raise ValueError(f"Unsupported intervention stage: {stage}")
    transcript = str(replacement)
    order_id = "74821" if "74821" in transcript else "7421"
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": order_id}},
                        [StageResult("asr", transcript), StageResult("tool", {"order_id": order_id})])


def region_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted bug: a dropout in the 1.2–1.4 s region corrupts the ID."""
    audio = read_wav(audio_path)
    lo, hi = int(1.2 * audio.rate), min(len(audio.samples), int(1.4 * audio.rate))
    broken = bool(hi > lo and np.mean(np.abs(audio.samples[lo:hi])) < 0.025)
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421" if broken else "74821"}})


def combination_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted bug: a long pause and broadband noise must occur together."""
    audio = read_wav(audio_path)
    prefix = audio.samples[: max(32, int(0.2 * audio.rate)), 0]
    spectrum = np.abs(np.fft.rfft(prefix)) ** 2
    frequencies = np.fft.rfftfreq(len(prefix), 1 / audio.rate)
    high_fraction = float(spectrum[frequencies > min(2000, audio.rate / 4)].sum() / max(1e-12, spectrum.sum()))
    broken = _longest_low_energy_seconds(audio) >= 0.5 and high_fraction > 0.0001
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421" if broken else "74821"}})


def triple_combination_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted bug requiring a pause, noise, and speed change together."""
    audio = read_wav(audio_path)
    prefix = audio.samples[: max(32, int(0.2 * audio.rate)), 0]
    spectrum = np.abs(np.fft.rfft(prefix)) ** 2
    frequencies = np.fft.rfftfreq(len(prefix), 1 / audio.rate)
    high_fraction = float(spectrum[frequencies > min(2000, audio.rate / 4)].sum()
                          / max(1e-12, spectrum.sum()))
    broken = (_longest_low_energy_seconds(audio) >= 0.45 and high_fraction > 0.0005
              and 3.62 <= audio.duration <= 3.70)
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421" if broken else "74821"}})


def nonmonotonic_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted bug: 300 and 500 ms pauses fail, while 400 ms passes."""
    pause_ms = _longest_quiet_seconds(audio_path) * 1000
    broken = (290 <= pause_ms <= 310) or (490 <= pause_ms <= 510)
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421" if broken else "74821"}})


def stream_delay_target(stream: StreamPlan, scenario: Scenario) -> TargetResult:
    """Planted streaming bug: one chunk delayed by at least 250 ms corrupts the order ID."""
    summary = transport_summary(stream)
    broken = summary["max_delay_ms"] >= 250
    order_id = "7421" if broken else "74821"
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": order_id}},
                        [StageResult("transport", summary), StageResult("asr", f"order {order_id}")])


def marker_failure_target(audio_path, scenario: Scenario) -> TargetResult:
    """Planted production-like failure: a loud marker anywhere in the clip corrupts output."""
    audio = read_wav(audio_path)
    broken = float(np.max(np.abs(audio.samples))) > 0.5
    return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421" if broken else "74821"}})


def dual_failure_target(audio_path, scenario: Scenario) -> TargetResult:
    """Two independent planted failures for multi-failure discovery tests."""
    audio = read_wav(audio_path)
    if _longest_quiet_seconds(audio_path) >= 0.5:
        output = {"tool": "lookup_order", "arguments": {"order_id": "7421"}}
    elif float(np.sqrt(np.mean(audio.samples ** 2))) < 0.02:
        output = {"tool": "cancel_order", "arguments": {"order_id": "74821"}}
    else:
        output = {"tool": "lookup_order", "arguments": {"order_id": "74821"}}
    return TargetResult(output)
