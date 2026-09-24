"""Mutation-program reduction without assuming monotonic model behavior."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .models import Mutation


def _duration(mutation: Mutation, base_duration: float) -> float:
    p = mutation.parameters
    if mutation.kind == "silence":
        return float(p["duration_ms"]) / 1000
    if mutation.kind == "dropout" and "duration_ms" in p:
        return float(p["duration_ms"]) / 1000
    if mutation.kind == "crop":
        return max(0, float(p["end"]) - float(p["start"]))
    if mutation.kind in ("packet_loss", "jitter", "chunk_delay", "reorder"):
        return 0
    return max(0, float(p.get("end", base_duration)) - float(p.get("start", 0)))


def _strength(mutation: Mutation) -> float:
    p, kind = mutation.parameters, mutation.kind
    if kind in ("silence", "dropout"):
        return float(p.get("duration_ms", 0))
    if kind == "noise":
        return max(0, 60 - float(p["snr_db"]))
    if kind == "gain":
        return abs(float(p["db"]))
    if kind == "clipping":
        return 1 - float(p["threshold"])
    if kind == "speed":
        return abs(float(p["rate"]) - 1)
    if kind == "bandwidth":
        return 20000 / float(p["cutoff_hz"])
    if kind == "quantize":
        return 16 - int(p["bits"])
    if kind == "resample":
        return 48000 / int(p["target_rate"])
    if kind == "codec":
        return 1
    if kind == "packet_loss":
        return float(p["loss_rate"])
    if kind == "jitter":
        return float(p["max_jitter_ms"])
    if kind == "chunk_delay":
        return float(p["delay_ms"])
    if kind == "reorder":
        return int(p.get("distance", 1))
    return 0


def complexity(program: list[Mutation], base_duration: float) -> tuple:
    return (len(program), len(program), round(sum(_duration(m, base_duration) for m in program), 6),
            round(sum(_strength(m) for m in program), 6),
            round(base_duration + sum(float(m.parameters.get("duration_ms", 0)) / 1000
                                      for m in program if m.kind == "silence"), 6))


def _replace(program: list[Mutation], index: int, params: dict[str, Any]) -> list[Mutation]:
    result = list(program)
    result[index] = Mutation(result[index].kind, params)
    return result


def proposals(program: list[Mutation], base_duration: float, sample_rate: int = 16000) -> Iterator[list[Mutation]]:
    # Phase 1: remove whole operators, including larger chunks.
    for size in range(len(program) - 1, 0, -1):
        for start in range(len(program) - size + 1):
            yield program[:start] + program[start + size:]
    for index in range(len(program)):
        yield program[:index] + program[index + 1:]

    for index, mutation in enumerate(program):
        p, kind = mutation.parameters, mutation.kind
        # Phase 2: reduce temporal support for region-based mutations.
        if kind not in ("silence", "speed") and ("start" in p or "end" in p):
            start = float(p.get("start", 0))
            end = float(p.get("end", base_duration))
            if kind == "dropout" and "duration_ms" in p:
                end = start + float(p["duration_ms"]) / 1000
            span = end - start
            if span > 0.012:
                for a, b in ((start, start + span / 2), (start + span / 2, end),
                             (start + span / 4, end - span / 4)):
                    q = dict(p)
                    q["start"] = round(a, 6)
                    if kind == "dropout" and "duration_ms" in p:
                        q["duration_ms"] = round((b - a) * 1000, 3)
                    else:
                        q["end"] = round(b, 6)
                    yield _replace(program, index, q)

        # Phase 3: finite, non-monotonic strength probes toward identity.
        if kind in ("silence", "dropout") and "duration_ms" in p:
            current = float(p["duration_ms"])
            values = {round(current * factor, 3) for factor in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95)}
            values |= {round(current - step, 3) for step in (50, 20, 10, 5, 1)}
            for value in sorted(v for v in values if 0 < v < current):
                yield _replace(program, index, {**p, "duration_ms": value})
        elif kind == "noise":
            current = float(p["snr_db"])
            for value in (current + 2, current + 5, current + 10):
                if value <= 60:
                    yield _replace(program, index, {**p, "snr_db": value})
        elif kind == "gain":
            current = float(p["db"])
            for factor in (0.5, 0.75, 0.9):
                yield _replace(program, index, {**p, "db": round(current * factor, 3)})
        elif kind == "clipping":
            current = float(p["threshold"])
            for value in (current + (1 - current) * 0.25, current + (1 - current) * 0.5):
                if value < 1:
                    yield _replace(program, index, {**p, "threshold": round(value, 4)})
        elif kind == "speed":
            current = float(p["rate"])
            for factor in (0.5, 0.75):
                yield _replace(program, index, {**p, "rate": round(1 + (current - 1) * factor, 5)})
        elif kind == "bandwidth":
            current = float(p["cutoff_hz"])
            for value in (current * 1.2, current * 1.5):
                yield _replace(program, index, {**p, "cutoff_hz": round(value)})
        elif kind == "quantize":
            current = int(p["bits"])
            for value in (current + 1, current + 2):
                if value <= 16:
                    yield _replace(program, index, {**p, "bits": value})
        elif kind == "resample":
            current = int(p["target_rate"])
            for value in (round(current * 1.25), round(current * 1.5)):
                if value < sample_rate:
                    yield _replace(program, index, {**p, "target_rate": value})
        elif kind == "packet_loss":
            current = float(p["loss_rate"])
            for factor in (0.5, 0.7, 0.85, 0.95):
                value = round(current * factor, 5)
                if 0 < value < current:
                    yield _replace(program, index, {**p, "loss_rate": value})
        elif kind == "jitter":
            current = float(p["max_jitter_ms"])
            for factor in (0.5, 0.7, 0.85, 0.95):
                value = round(current * factor, 3)
                if 0 < value < current:
                    yield _replace(program, index, {**p, "max_jitter_ms": value})
        elif kind == "chunk_delay":
            current = float(p["delay_ms"])
            values = {round(current * factor, 3) for factor in (0.5, 0.7, 0.8, 0.9, 0.95)}
            values |= {round(current - step, 3) for step in (50, 20, 10, 5, 1)}
            for value in sorted(value for value in values if 0 < value < current):
                yield _replace(program, index, {**p, "delay_ms": value})
        elif kind == "reorder":
            current = int(p.get("distance", 1))
            for value in range(1, current):
                yield _replace(program, index, {**p, "distance": value})
