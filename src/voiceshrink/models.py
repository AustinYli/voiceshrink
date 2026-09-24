from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config import load_data


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL_SAME = "FAIL_SAME"
    FAIL_OTHER = "FAIL_OTHER"
    INVALID = "INVALID"
    TARGET_ERROR = "TARGET_ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class Mutation:
    kind: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.kind, **self.parameters}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mutation:
        copy = data.copy()
        kind = copy.pop("type", copy.pop("kind", None))
        if not kind:
            raise ValueError("Mutation needs a type")
        return cls(kind, copy)


@dataclass
class StageResult:
    name: str
    output: Any


@dataclass
class TargetResult:
    final_output: Any
    stages: list[StageResult] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> TargetResult:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict) and "final_output" in value:
            stages = [StageResult(**s) if isinstance(s, dict) else s for s in value.get("stages", [])]
            return cls(value["final_output"], stages, value.get("metadata", {}))
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureFingerprint:
    component: str
    category: str
    path: str
    expected: Any
    observed: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Scenario:
    name: str
    audio: Path
    expected: dict[str, Any]
    target: dict[str, Any]
    oracle: dict[str, Any] = field(default_factory=dict)
    search: dict[str, Any] = field(default_factory=dict)
    evaluation: dict[str, Any] = field(default_factory=dict)
    streaming: dict[str, Any] = field(default_factory=dict)
    investigate: dict[str, Any] = field(default_factory=dict)
    privacy: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        path = Path(path).resolve()
        data = load_data(path)
        audio = Path(data["audio"])
        if not audio.is_absolute():
            audio = (path.parent / audio).resolve()
        if not audio.is_file():
            raise FileNotFoundError(f"Base audio not found: {audio}")
        expected = data.get("expected")
        target = data.get("target")
        if not isinstance(expected, dict) or not isinstance(target, dict):
            raise ValueError("Scenario needs expected and target mappings")
        return cls(str(data.get("name") or path.stem), audio, expected, target,
                   data.get("oracle") or {}, data.get("search") or {},
                   data.get("evaluation") or {}, data.get("streaming") or {},
                   data.get("investigate") or {}, data.get("privacy") or {}, path)

    def to_dict(self, audio: str | None = None) -> dict[str, Any]:
        return {"name": self.name, "audio": audio or str(self.audio), "expected": self.expected,
                "target": self.target, "oracle": self.oracle, "search": self.search,
                "evaluation": self.evaluation, "streaming": self.streaming,
                "investigate": self.investigate, "privacy": self.privacy}


@dataclass
class Evaluation:
    mutations: list[Mutation]
    outcome: Outcome
    fingerprint: FailureFingerprint | None
    result: TargetResult | None
    reproduction_rate: float
    trials: int
    errors: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"mutations": [m.to_dict() for m in self.mutations], "outcome": self.outcome.value,
                "fingerprint": self.fingerprint.to_dict() if self.fingerprint else None,
                "result": self.result.to_dict() if self.result else None,
                "reproduction_rate": self.reproduction_rate, "trials": self.trials, "errors": self.errors,
                "runtime_seconds": self.runtime_seconds}
