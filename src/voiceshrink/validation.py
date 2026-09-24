"""Side-effect-free scenario validation shared by the CLI and engine."""

from __future__ import annotations

import math
from collections.abc import Mapping
from urllib.parse import urlparse

from .models import Scenario


ACOUSTIC_FAMILIES = {
    "silence", "dropout", "noise", "gain", "clipping", "speed",
    "bandwidth", "resample", "codec", "quantize",
}
TRANSPORT_FAMILIES = {"packet_loss", "jitter", "chunk_delay", "reorder"}
ALL_FAMILIES = ACOUSTIC_FAMILIES | TRANSPORT_FAMILIES


def validate_scenario(scenario: Scenario) -> None:
    errors: list[str] = []

    def positive(mapping: Mapping, section: str, name: str, default, *, integer: bool = False) -> None:
        value = mapping.get(name, default)
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            errors.append(f"{section}.{name} must be numeric")
            return
        if isinstance(value, bool) or not math.isfinite(parsed):
            errors.append(f"{section}.{name} must be a finite number")
        elif integer and not parsed.is_integer():
            errors.append(f"{section}.{name} must be an integer")
        elif parsed <= 0:
            errors.append(f"{section}.{name} must be positive")

    if not scenario.expected:
        errors.append("expected must not be empty")

    target_type = scenario.target.get("type")
    required = {"python": "callable", "command": "command", "http": "url"}
    if target_type not in {"python", "command", "http", "faster-whisper"}:
        errors.append(f"target.type is unsupported: {target_type!r}")
    elif target_type in required and not scenario.target.get(required[target_type]):
        errors.append(f"target.{required[target_type]} is required for {target_type}")
    if target_type == "command":
        command = scenario.target.get("command")
        if not isinstance(command, (str, list)) or not command:
            errors.append("target.command must be a non-empty string or list")
    if target_type == "http":
        parsed = urlparse(str(scenario.target.get("url", "")))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append("target.url must be an absolute HTTP or HTTPS URL")
    positive(scenario.target, "target", "timeout", 60)

    positive(scenario.search, "search", "max_runs", 200, integer=True)
    positive(scenario.search, "search", "max_concurrency", 1, integer=True)
    positive(scenario.search, "search", "max_failures", 1, integer=True)
    try:
        max_pairs_raw = scenario.search.get("max_pairs", 60)
        max_pairs = float(max_pairs_raw)
        if isinstance(max_pairs_raw, bool) or not math.isfinite(max_pairs) or not max_pairs.is_integer():
            errors.append("search.max_pairs must be an integer")
        elif max_pairs < 0:
            errors.append("search.max_pairs cannot be negative")
    except (TypeError, ValueError):
        errors.append("search.max_pairs must be an integer")
    try:
        max_combinations_raw = scenario.search.get("max_combinations", 0)
        max_combinations = float(max_combinations_raw)
        if (isinstance(max_combinations_raw, bool) or not math.isfinite(max_combinations)
                or not max_combinations.is_integer()):
            errors.append("search.max_combinations must be an integer")
        elif max_combinations < 0:
            errors.append("search.max_combinations cannot be negative")
    except (TypeError, ValueError):
        errors.append("search.max_combinations must be an integer")
    try:
        combination_size_raw = scenario.search.get("combination_size", 3)
        combination_size = float(combination_size_raw)
        if (isinstance(combination_size_raw, bool) or not math.isfinite(combination_size)
                or not combination_size.is_integer() or not 3 <= combination_size <= 6):
            errors.append("search.combination_size must be an integer from 3 to 6")
    except (TypeError, ValueError):
        errors.append("search.combination_size must be an integer from 3 to 6")
    try:
        cost = float(scenario.search.get("cost_per_run", 0))
        if not math.isfinite(cost):
            errors.append("search.cost_per_run must be finite")
        elif cost < 0:
            errors.append("search.cost_per_run cannot be negative")
    except (TypeError, ValueError):
        errors.append("search.cost_per_run must be numeric")
    families = scenario.search.get("families", list(ACOUSTIC_FAMILIES))
    if not isinstance(families, list) or not families:
        errors.append("search.families must be a non-empty list")
    elif not all(isinstance(value, str) for value in families):
        errors.append("search.families values must be strings")
    else:
        unknown = sorted(set(families) - ALL_FAMILIES)
        if unknown:
            errors.append(f"search.families contains unsupported values: {unknown}")

    positive(scenario.evaluation, "evaluation", "trials", 1, integer=True)
    positive(scenario.evaluation, "evaluation", "baseline_trials",
             scenario.evaluation.get("trials", 1), integer=True)
    try:
        threshold = float(scenario.evaluation.get("failure_threshold", 1.0))
        if not 0 < threshold <= 1:
            errors.append("evaluation.failure_threshold must be in (0, 1]")
    except (TypeError, ValueError):
        errors.append("evaluation.failure_threshold must be numeric")

    positive(scenario.streaming, "streaming", "chunk_ms", 20)
    positive(scenario.investigate, "investigate", "min_window_ms", 250)
    positive(scenario.investigate, "investigate", "boundary_step_ms", 10)

    equivalence = scenario.oracle.get("equivalence", "same_category")
    if equivalence not in {"same_category", "exact", "custom"}:
        errors.append("oracle.equivalence must be same_category, exact, or custom")
    if equivalence == "custom" and not scenario.oracle.get("callable"):
        errors.append("oracle.callable is required for custom equivalence")

    for name in ("redact_report_outputs", "hide_report_audio", "cache_results"):
        if name in scenario.privacy and not isinstance(scenario.privacy[name], bool):
            errors.append(f"privacy.{name} must be true or false")

    if errors:
        raise ValueError("Invalid scenario:\n- " + "\n- ".join(errors))
