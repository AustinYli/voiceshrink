"""Expected-output checking and same-failure equivalence."""

from __future__ import annotations

import sys
from typing import Any

from .models import FailureFingerprint, Scenario, TargetResult
from .targets import _load_callable


def _first_difference(expected: Any, actual: Any, path: str = "") -> tuple[str, Any, Any, str] | None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return path or "final_output", expected, actual, "wrong_type"
        for key, value in expected.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                return child, value, None, "missing_field"
            difference = _first_difference(value, actual[key], child)
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return path, expected, actual, "wrong_type"
        if len(expected) != len(actual):
            return path, expected, actual, "wrong_length"
        for index, value in enumerate(expected):
            difference = _first_difference(value, actual[index], f"{path}[{index}]")
            if difference:
                return difference
        return None
    if expected != actual:
        category = "wrong_tool_argument" if path.startswith("arguments.") else "wrong_value"
        if path in ("tool", "tool.name"):
            category = "wrong_tool"
        return path or "final_output", expected, actual, category
    return None


def check(scenario: Scenario, result: TargetResult) -> FailureFingerprint | None:
    expected = scenario.expected
    output = result.final_output
    if "transcript_contains" in expected:
        needle = str(expected["transcript_contains"])
        transcript = output.get("transcript", "") if isinstance(output, dict) else str(output or "")
        if needle not in transcript:
            return FailureFingerprint("final_output", "missing_transcript_text", "transcript", needle, transcript)
        return None
    if "equals" in expected:
        difference = _first_difference(expected["equals"], output)
    else:
        shape = dict(expected)
        if "tool" in shape and isinstance(shape["tool"], dict):
            shape["tool"] = shape["tool"].get("name")
        difference = _first_difference(shape, output)
    if difference is None:
        return None
    path, wanted, observed, category = difference
    if output is None:
        category = "missing_output"
    return FailureFingerprint("final_output", category, path, wanted, observed)


def same_failure(scenario: Scenario, reference: FailureFingerprint,
                 candidate: FailureFingerprint | None) -> bool:
    if candidate is None:
        return False
    mode = scenario.oracle.get("equivalence", "same_category")
    if mode == "exact":
        return reference == candidate
    if mode == "same_category":
        return (reference.component, reference.category, reference.path) == (
            candidate.component, candidate.category, candidate.path)
    if mode == "custom":
        reference_name = scenario.oracle.get("callable")
        if not reference_name:
            raise ValueError("Custom equivalence needs oracle.callable")
        added = str(scenario.path.parent) if scenario.path else None
        if added:
            sys.path.insert(0, added)
        try:
            return bool(_load_callable(reference_name)(reference, candidate))
        finally:
            if added:
                sys.path.remove(added)
    raise ValueError(f"Unknown equivalence mode: {mode}")


def earliest_divergence(baseline: TargetResult | None, failure: TargetResult | None) -> str | None:
    if not baseline or not failure or not baseline.stages or not failure.stages:
        return None
    for left, right in zip(baseline.stages, failure.stages):
        if left.name != right.name or left.output != right.output:
            return right.name
    if len(baseline.stages) != len(failure.stages):
        return "stage count"
    return None
