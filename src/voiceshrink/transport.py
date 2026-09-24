"""Deterministic streaming transport plans, separate from acoustic WAV mutations."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .audio import Audio
from .models import Mutation


TRANSPORT_KINDS = {"packet_loss", "jitter", "chunk_delay", "reorder"}


@dataclass
class StreamChunk:
    sequence: int
    timestamp_ms: float
    delivery_ms: float
    dropped: bool
    pcm_base64: str


@dataclass
class StreamPlan:
    sample_rate: int
    channels: int
    sample_width: int
    chunk_ms: float
    chunks: list[StreamChunk]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def delivered(self) -> list[StreamChunk]:
        return sorted((chunk for chunk in self.chunks if not chunk.dropped),
                      key=lambda chunk: (chunk.delivery_ms, chunk.sequence))


def make_stream(audio: Audio, chunk_ms: float = 20) -> StreamPlan:
    if chunk_ms <= 0:
        raise ValueError("streaming.chunk_ms must be positive")
    frame_count = max(1, round(audio.rate * chunk_ms / 1000))
    pcm = np.rint(np.clip(audio.samples, -1, 32767 / 32768) * 32768).astype("<i2")
    chunks: list[StreamChunk] = []
    for sequence, start in enumerate(range(0, len(pcm), frame_count)):
        timestamp = start * 1000 / audio.rate
        chunks.append(StreamChunk(sequence, timestamp, timestamp, False,
                                  base64.b64encode(pcm[start:start + frame_count].tobytes()).decode("ascii")))
    return StreamPlan(audio.rate, audio.samples.shape[1], 2, chunk_ms, chunks)


def _chunk_index(params: dict[str, Any], count: int) -> int:
    if not count:
        return 0
    if "chunk_index" in params:
        return max(0, min(count - 1, int(params["chunk_index"])))
    position = float(params.get("position", 0.5))
    if not 0 <= position <= 1:
        raise ValueError("Transport position must be in [0, 1]")
    return min(count - 1, round(position * (count - 1)))


def apply_transport(plan: StreamPlan, mutations: list[Mutation]) -> StreamPlan:
    chunks = [StreamChunk(**asdict(chunk)) for chunk in plan.chunks]
    for mutation in mutations:
        params = mutation.parameters
        if mutation.kind == "packet_loss":
            rate = float(params["loss_rate"])
            if not 0 <= rate <= 1:
                raise ValueError("packet_loss loss_rate must be in [0, 1]")
            rng = np.random.default_rng(int(params.get("seed", 0)))
            for chunk, drop in zip(chunks, rng.random(len(chunks)) < rate):
                chunk.dropped = chunk.dropped or bool(drop)
        elif mutation.kind == "jitter":
            maximum = float(params["max_jitter_ms"])
            if maximum < 0:
                raise ValueError("jitter max_jitter_ms cannot be negative")
            rng = np.random.default_rng(int(params.get("seed", 0)))
            for chunk, offset in zip(chunks, rng.uniform(-maximum, maximum, len(chunks))):
                chunk.delivery_ms = max(0.0, chunk.delivery_ms + float(offset))
        elif mutation.kind == "chunk_delay":
            delay = float(params["delay_ms"])
            if delay < 0:
                raise ValueError("chunk_delay delay_ms cannot be negative")
            if chunks:
                chunks[_chunk_index(params, len(chunks))].delivery_ms += delay
        elif mutation.kind == "reorder":
            distance = int(params.get("distance", 1))
            if distance < 1:
                raise ValueError("reorder distance must be positive")
            first = _chunk_index(params, len(chunks))
            second = min(len(chunks) - 1, first + distance)
            if chunks and first != second:
                chunks[first].delivery_ms, chunks[second].delivery_ms = chunks[second].delivery_ms, chunks[first].delivery_ms
        else:
            raise ValueError(f"Unknown transport mutation: {mutation.kind}")
    return StreamPlan(plan.sample_rate, plan.channels, plan.sample_width, plan.chunk_ms, chunks)


def transport_summary(plan: StreamPlan) -> dict[str, Any]:
    delivered = plan.delivered
    delays = [chunk.delivery_ms - chunk.timestamp_ms for chunk in delivered]
    sequence = [chunk.sequence for chunk in delivered]
    return {"total_chunks": len(plan.chunks), "delivered_chunks": len(delivered),
            "dropped_chunks": sum(chunk.dropped for chunk in plan.chunks),
            "max_delay_ms": max(delays, default=0.0),
            "delivery_reorders": sum(1 for left, right in zip(sequence, sequence[1:]) if right < left)}
