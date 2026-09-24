"""Escalating, deterministic discovery candidate generation."""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator

import numpy as np

from .audio import Audio
from .models import Mutation, Scenario


def temporal_positions(audio: Audio, maximum: int = 8) -> list[float]:
    """Find deterministic speech/quiet transitions and quiet-region midpoints."""
    frame_count = max(1, round(audio.rate * 0.02))
    mono = audio.samples.mean(axis=1)
    count = len(mono) // frame_count
    if count < 2:
        return []
    frames = mono[:count * frame_count].reshape(count, frame_count)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    peak = float(np.max(rms))
    if peak <= 0:
        return []
    threshold = max(0.003, min(0.05, peak * 0.15))
    active = rms > threshold
    positions = [index * frame_count / audio.rate for index in range(1, count)
                 if bool(active[index]) != bool(active[index - 1])]
    start = None
    for index, value in enumerate(active.tolist() + [True]):
        if not value and start is None:
            start = index
        elif value and start is not None:
            if index - start >= 3:
                positions.append((start + index) * frame_count / (2 * audio.rate))
            start = None
    positions = sorted({round(position, 4) for position in positions
                        if 0.02 <= position <= audio.duration - 0.02})
    if len(positions) <= maximum:
        return positions
    indices = np.linspace(0, len(positions) - 1, maximum, dtype=int)
    return [positions[int(index)] for index in indices]


def candidates(scenario: Scenario, duration: float, sample_rate: int = 16000,
               boundary_positions: list[float] | None = None) -> Iterator[list[Mutation]]:
    config = scenario.search
    families = config.get("families", ["silence", "dropout", "noise", "gain", "clipping", "speed", "bandwidth", "resample", "codec", "quantize"])
    positions = [round(duration * fraction, 4) for fraction in (0.2, 0.5, 0.8)]
    learned_positions = [round(float(position), 4) for position in (boundary_positions or [])
                         if 0 <= float(position) <= duration]
    region_positions = sorted(set(
        [round(duration * fraction, 4) for fraction in (0, 0.1, 0.25, 0.4, 0.6, 0.75, 0.9)]
        + learned_positions))
    singles: list[Mutation] = []

    # Stage A: cheap single mutations in an escalating strength order.
    for kind in families:
        if kind == "silence":
            singles.extend(Mutation(kind, {"position": positions[1], "duration_ms": ms})
                           for ms in (100, 250, 450, 750, 1000))
        elif kind == "dropout":
            singles.extend(Mutation(kind, {"start": positions[1], "duration_ms": ms})
                           for ms in (40, 100, 250, 500))
        elif kind == "noise":
            singles.extend(Mutation(kind, {"snr_db": db, "seed": int(config.get("seed", 1))})
                           for db in (30, 20, 12, 6, 0))
        elif kind == "gain":
            singles.extend(Mutation(kind, {"db": db}) for db in (-3, -6, -12, -24))
        elif kind == "clipping":
            singles.extend(Mutation(kind, {"threshold": threshold}) for threshold in (0.7, 0.4, 0.2, 0.1))
        elif kind == "speed":
            singles.extend(Mutation(kind, {"rate": rate}) for rate in (0.95, 0.85, 1.05, 1.2))
        elif kind == "bandwidth":
            singles.extend(Mutation(kind, {"cutoff_hz": cutoff}) for cutoff in (3500, 2000, 1000)
                           if cutoff < sample_rate / 2)
        elif kind == "quantize":
            singles.extend(Mutation(kind, {"bits": bits}) for bits in (12, 8, 5, 3))
        elif kind == "resample":
            singles.extend(Mutation(kind, {"target_rate": target_rate}) for target_rate in (12000, 8000, 4000)
                           if target_rate < sample_rate)
        elif kind == "codec":
            singles.append(Mutation(kind, {"codec": "mulaw"}))
        elif kind == "packet_loss":
            singles.extend(Mutation(kind, {"loss_rate": rate, "seed": int(config.get("seed", 1))})
                           for rate in (0.01, 0.03, 0.08, 0.15, 0.3))
        elif kind == "jitter":
            singles.extend(Mutation(kind, {"max_jitter_ms": amount, "seed": int(config.get("seed", 1))})
                           for amount in (5, 15, 40, 100, 250))
        elif kind == "chunk_delay":
            singles.extend(Mutation(kind, {"position": 0.5, "delay_ms": amount})
                           for amount in (25, 75, 150, 400, 800))
        elif kind == "reorder":
            singles.extend(Mutation(kind, {"position": 0.5, "distance": distance})
                           for distance in (1, 2, 4, 8))
        else:
            raise ValueError(f"Unknown search family: {kind}")
    for mutation in singles:
        yield [mutation]

    # Stage B: same operators at distinct audio regions.
    for kind in families:
        if kind == "silence":
            for position in region_positions:
                for ms in (250, 500, 750, 1000):
                    yield [Mutation(kind, {"position": position, "duration_ms": ms})]
        elif kind == "dropout":
            for position in region_positions:
                for ms in (40, 100, 250, 500):
                    yield [Mutation(kind, {"start": position, "duration_ms": ms})]
        elif kind in ("noise", "gain", "clipping", "bandwidth", "resample", "codec", "quantize"):
            base = next((m for m in singles if m.kind == kind), None)
            if base:
                regions = [(0, duration / 2), (duration / 2, duration)]
                regions.extend((max(0, position - 0.25), min(duration, position + 0.25))
                               for position in learned_positions)
                for start, end in regions:
                    if end - start <= 0.01:
                        continue
                    yield [Mutation(kind, {**base.parameters, "start": round(start, 4), "end": round(end, 4)})]
        elif kind in ("chunk_delay", "reorder"):
            base = next((m for m in singles if m.kind == kind), None)
            if base:
                for position in (0.2, 0.8):
                    yield [Mutation(kind, {**base.parameters, "position": position})]

    # Stage C: bounded pairwise combinations after all singles.
    max_pairs = int(config.get("max_pairs", 60))
    yielded_pairs = 0
    if max_pairs > 0:
        for first, second in itertools.combinations(singles, 2):
            if first.kind == second.kind:
                continue
            yield [first, second]
            yielded_pairs += 1
            if yielded_pairs >= max_pairs:
                break

    # Optional bounded higher-order exploration. Disabled by default because each
    # candidate can be expensive; a seed makes enabled searches reproducible.
    max_combinations = int(config.get("max_combinations", 0))
    combination_size = int(config.get("combination_size", 3))
    if max_combinations > 0 and combination_size >= 3:
        by_kind: dict[str, list[Mutation]] = {}
        for mutation in singles:
            by_kind.setdefault(mutation.kind, []).append(mutation)
        family_sets = list(itertools.combinations(by_kind, combination_size))
        if family_sets:
            rng = random.Random(int(config.get("seed", 1)))
            selected: list[list[Mutation]] = []
            seen: set[tuple[str, ...]] = set()
            attempts = max(100, max_combinations * 50)
            for _ in range(attempts):
                families = rng.choice(family_sets)
                program = [rng.choice(by_kind[family]) for family in families]
                label = tuple(repr(mutation.to_dict()) for mutation in program)
                if label in seen:
                    continue
                seen.add(label)
                selected.append(program)
                if len(selected) >= max_combinations:
                    break
            yield from selected
