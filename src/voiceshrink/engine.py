"""Budgeted evaluation, persistent cache, discovery, and minimization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .audio import Audio, read_wav, render, write_wav
from .models import Evaluation, FailureFingerprint, Mutation, Outcome, Scenario, TargetResult
from .oracle import check, earliest_divergence, same_failure
from .search import candidates, temporal_positions
from .shrink import complexity, proposals
from .targets import Target, create_target
from .transport import TRANSPORT_KINDS, apply_transport, make_stream
from .validation import validate_scenario


class BudgetExceeded(Exception):
    pass


class InvalidSeed(Exception):
    pass


class NotFailing(Exception):
    pass


def _canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class Discovery:
    baseline: Evaluation
    initial: Evaluation | None
    minimal: Evaluation | None
    runs: int
    cache_hits: int
    duration_seconds: float
    stopped_by_budget: bool = False
    attribution: dict | None = None


@dataclass
class DiscoveryBatch:
    baseline: Evaluation
    failures: list[Discovery]
    runs: int
    cache_hits: int
    duration_seconds: float
    stopped_by_budget: bool = False


@dataclass
class Investigation:
    original: Evaluation
    reduced: Evaluation
    original_duration_seconds: float
    reduced_start_seconds: float
    reduced_end_seconds: float
    runs: int
    cache_hits: int
    duration_seconds: float
    stopped_by_budget: bool = False

    @property
    def reduction_rate(self) -> float:
        remaining = self.reduced_end_seconds - self.reduced_start_seconds
        return 1 - remaining / self.original_duration_seconds


class Engine:
    def __init__(self, scenario: Scenario, target: Target | None = None, cache_dir: Path | None = None,
                 fresh: bool = False):
        validate_scenario(scenario)
        self.scenario = scenario
        self.target = target or create_target(scenario)
        self.audio: Audio = read_wav(scenario.audio)
        self.base_hash = hashlib.sha256(scenario.audio.read_bytes()).hexdigest()
        self.max_runs = int(scenario.search.get("max_runs", 200))
        self.runs = 0
        self.cache_hits = 0
        self.fresh = fresh
        self.cache_dir = cache_dir or (scenario.path.parent if scenario.path else Path.cwd()) / ".voiceshrink"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.cache_dir / "cache.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS evaluations (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        self._temporary = tempfile.TemporaryDirectory(prefix="voiceshrink-")
        self.workdir = Path(self._temporary.name)

    def close(self) -> None:
        self.db.close()
        self._temporary.cleanup()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _key(self, program: list[Mutation], trials: int) -> str:
        config = {"version": __version__, "base": self.base_hash, "scenario": self.scenario.name,
                  "target": self.scenario.target, "expected": self.scenario.expected,
                  "oracle": self.scenario.oracle, "streaming": self.scenario.streaming,
                  "investigate": self.scenario.investigate, "privacy": self.scenario.privacy,
                  "program": [m.to_dict() for m in program], "trials": trials}
        return hashlib.sha256(_canonical(config).encode()).hexdigest()

    def _trials(self, program: list[Mutation], trials: int) -> list[dict]:
        key = self._key(program, trials)
        cache_results = bool(self.scenario.privacy.get("cache_results", True))
        if not self.fresh and cache_results:
            row = self.db.execute("SELECT value FROM evaluations WHERE key=?", (key,)).fetchone()
            if row:
                self.cache_hits += 1
                return json.loads(row[0])
        if self.runs + trials > self.max_runs:
            raise BudgetExceeded(f"Target execution budget ({self.max_runs}) exhausted")
        acoustic = [mutation for mutation in program if mutation.kind not in TRANSPORT_KINDS]
        transport = [mutation for mutation in program if mutation.kind in TRANSPORT_KINDS]
        rendered = render(self.audio, acoustic)
        path = self.workdir / f"{key}.wav"
        write_wav(path, rendered)
        streaming = bool(transport) or bool(self.scenario.streaming.get("enabled", False))
        if streaming and not self.target.can_stream:
            raise ValueError("Scenario uses transport mutations but target has no streaming adapter")
        stream = None
        if streaming:
            stream = apply_transport(make_stream(rendered, float(self.scenario.streaming.get("chunk_ms", 20))), transport)

        def run_once() -> dict:
            started = time.monotonic()
            try:
                observed = self.target.execute_stream(stream, self.scenario) if stream is not None else self.target.execute(path, self.scenario)
                result = TargetResult.from_value(observed)
                fingerprint = check(self.scenario, result)
                return {"result": result.to_dict(), "fingerprint": fingerprint.to_dict() if fingerprint else None,
                        "error": None, "runtime_seconds": time.monotonic() - started}
            except Exception as exc:
                return {"result": None, "fingerprint": None,
                        "error": f"{type(exc).__name__}: {exc}", "runtime_seconds": time.monotonic() - started}

        concurrency = max(1, min(trials, int(self.scenario.search.get("max_concurrency", 1))))
        if concurrency == 1:
            attempts = [run_once() for _ in range(trials)]
        else:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="voiceshrink") as pool:
                attempts = list(pool.map(lambda _: run_once(), range(trials)))
        self.runs += trials
        if cache_results:
            self.db.execute("INSERT OR REPLACE INTO evaluations (key,value) VALUES (?,?)", (key, _canonical(attempts)))
            self.db.commit()
        return attempts

    def evaluate(self, program: list[Mutation], reference: FailureFingerprint | None = None,
                 trials: int | None = None) -> Evaluation:
        count = int(trials or self.scenario.evaluation.get("trials", 1))
        if count < 1:
            raise ValueError("evaluation.trials must be positive")
        threshold = float(self.scenario.evaluation.get("failure_threshold", 1.0))
        if not 0 < threshold <= 1:
            raise ValueError("failure_threshold must be in (0, 1]")
        attempts = self._trials(program, count)
        errors = [attempt["error"] for attempt in attempts if attempt["error"]]
        fingerprints = [FailureFingerprint(**a["fingerprint"]) for a in attempts if a["fingerprint"]]
        if reference:
            matching = [fp for fp in fingerprints if same_failure(self.scenario, reference, fp)]
            rate = len(matching) / count
            fingerprint = matching[0] if matching else (fingerprints[0] if fingerprints else None)
            if rate >= threshold:
                outcome = Outcome.FAIL_SAME
            elif fingerprints:
                outcome = Outcome.FAIL_OTHER if len(fingerprints) / count >= threshold else Outcome.INCONCLUSIVE
            elif errors:
                outcome = Outcome.TARGET_ERROR
            else:
                outcome = Outcome.PASS
            preferred = next((a for a in attempts if a["fingerprint"] and
                              same_failure(self.scenario, reference, FailureFingerprint(**a["fingerprint"]))), None)
        else:
            groups: dict[str, list[FailureFingerprint]] = {}
            for fp in fingerprints:
                label = _canonical([fp.component, fp.category, fp.path])
                groups.setdefault(label, []).append(fp)
            largest = max(groups.values(), key=len) if groups else []
            rate = len(largest) / count
            fingerprint = largest[0] if largest else None
            if rate >= threshold and largest:
                outcome = Outcome.FAIL_SAME
            elif errors:
                outcome = Outcome.TARGET_ERROR
            elif fingerprints:
                outcome = Outcome.INCONCLUSIVE
            else:
                outcome = Outcome.PASS
            preferred = next((a for a in attempts if fingerprint and a["fingerprint"] and
                              (a["fingerprint"]["component"], a["fingerprint"]["category"], a["fingerprint"]["path"]) ==
                              (fingerprint.component, fingerprint.category, fingerprint.path)), None)
        result_data = (preferred or next((a for a in attempts if a["result"] is not None), {})).get("result")
        result = TargetResult.from_value(result_data) if result_data is not None else None
        runtime = sum(float(attempt.get("runtime_seconds", 0)) for attempt in attempts)
        return Evaluation(program, outcome, fingerprint, result, rate, count, errors, runtime)

    def baseline(self) -> Evaluation:
        trials = int(self.scenario.evaluation.get("baseline_trials", self.scenario.evaluation.get("trials", 1)))
        value = self.evaluate([], trials=trials)
        if value.outcome != Outcome.PASS or value.errors:
            raise InvalidSeed(f"Clean audio did not reliably pass: {value.outcome.value}; "
                              f"fingerprint={value.fingerprint}; errors={value.errors}")
        return value

    def discover(self) -> Discovery:
        batch = self.discover_many(max_failures=1)
        if batch.failures:
            return batch.failures[0]
        return Discovery(batch.baseline, None, None, batch.runs, batch.cache_hits,
                         batch.duration_seconds, batch.stopped_by_budget)

    def discover_many(self, max_failures: int | None = None) -> DiscoveryBatch:
        started = time.monotonic()
        baseline = self.baseline()
        limit = int(max_failures or self.scenario.search.get("max_failures", 1))
        if limit < 1:
            raise ValueError("search.max_failures must be positive")
        failures: list[Discovery] = []
        references: list[FailureFingerprint] = []
        stopped = False
        boundaries = temporal_positions(self.audio)
        for program in candidates(self.scenario, self.audio.duration, self.audio.rate, boundaries):
            try:
                evaluation = self.evaluate(program)
            except BudgetExceeded:
                stopped = True
                break
            if evaluation.outcome != Outcome.FAIL_SAME or evaluation.fingerprint is None:
                continue
            if any(same_failure(self.scenario, reference, evaluation.fingerprint) for reference in references):
                continue
            references.append(evaluation.fingerprint)
            best, shrink_stopped = self.shrink(evaluation)
            attribution = self.attribute(baseline, best)
            failures.append(Discovery(baseline, evaluation, best, self.runs,
                                      self.cache_hits, time.monotonic() - started,
                                      shrink_stopped, attribution))
            if shrink_stopped:
                stopped = True
                break
            if len(failures) >= limit:
                break
        return DiscoveryBatch(baseline, failures, self.runs, self.cache_hits,
                              time.monotonic() - started, stopped)

    def investigate_failure(self) -> Investigation:
        """Find a short contiguous window of already-failing audio with the same failure fingerprint."""
        started = time.monotonic()
        original = self.evaluate([])
        if original.outcome != Outcome.FAIL_SAME or original.fingerprint is None:
            raise NotFailing(f"Input does not reliably reproduce a failure: {original.outcome.value}")
        reference = original.fingerprint
        minimum = max(0.01, float(self.scenario.investigate.get("min_window_ms", 250)) / 1000)
        start, end = 0.0, self.audio.duration
        best = original
        stopped = False
        seen: set[tuple[float, float]] = {(start, end)}

        while end - start > minimum + 1e-6:
            span = end - start
            intervals: list[tuple[float, float]] = []
            for retained in (0.5, 0.67, 0.8, 0.9):
                width = max(minimum, span * retained)
                if width >= span - 1e-6:
                    continue
                intervals.extend(((start, start + width), (end - width, end),
                                  (start + (span - width) / 2, end - (span - width) / 2)))
            improvements: list[tuple[float, float, Evaluation]] = []
            for left, right in intervals:
                left, right = round(left, 6), round(right, 6)
                if (left, right) in seen:
                    continue
                seen.add((left, right))
                try:
                    candidate = self.evaluate([Mutation("crop", {"start": left, "end": right})], reference)
                except BudgetExceeded:
                    stopped = True
                    break
                if candidate.outcome == Outcome.FAIL_SAME:
                    improvements.append((left, right, candidate))
            if stopped or not improvements:
                break
            start, end, best = min(improvements, key=lambda item: (item[1] - item[0], item[0]))

        step = max(0.001, float(self.scenario.investigate.get("boundary_step_ms", 10)) / 1000)
        changed = True
        while changed and not stopped and end - start > minimum + step:
            changed = False
            for left, right in ((start + step, end), (start, end - step)):
                if right - left < minimum:
                    continue
                left, right = round(left, 6), round(right, 6)
                if (left, right) in seen:
                    continue
                seen.add((left, right))
                try:
                    candidate = self.evaluate([Mutation("crop", {"start": left, "end": right})], reference)
                except BudgetExceeded:
                    stopped = True
                    break
                if candidate.outcome == Outcome.FAIL_SAME:
                    start, end, best = left, right, candidate
                    changed = True
                    break
        return Investigation(original, best, self.audio.duration, start, end, self.runs, self.cache_hits,
                             time.monotonic() - started, stopped)

    def attribute(self, baseline: Evaluation, failure: Evaluation) -> dict | None:
        """Run one intervention at the earliest observed divergent stage when supported."""
        stage = earliest_divergence(baseline.result, failure.result)
        if stage is None:
            return None
        evidence: dict = {"earliest_observed_divergence": stage, "intervention_supported": self.target.can_intervene,
                          "restored_expected_behavior": None}
        if any(mutation.kind in TRANSPORT_KINDS for mutation in failure.mutations):
            evidence.update({"intervention_supported": False,
                             "note": "Audio-stage intervention is unavailable for transport-plan failures"})
            return evidence
        if not self.target.can_intervene:
            return evidence
        if self.runs + 1 > self.max_runs:
            evidence["error"] = f"Target execution budget ({self.max_runs}) exhausted before intervention"
            return evidence
        baseline_stage = next((item for item in baseline.result.stages if item.name == stage), None)
        if baseline_stage is None:
            evidence["error"] = f"Known-good output for stage {stage!r} was unavailable"
            return evidence
        label = hashlib.sha256(_canonical([m.to_dict() for m in failure.mutations]).encode()).hexdigest()
        path = self.workdir / f"intervention-{label}.wav"
        write_wav(path, render(self.audio, failure.mutations))
        try:
            result = TargetResult.from_value(
                self.target.intervene(path, self.scenario, stage, baseline_stage.output))
            fingerprint = check(self.scenario, result)
            evidence.update({"replacement": baseline_stage.output, "result": result.to_dict(),
                             "failure_fingerprint": fingerprint.to_dict() if fingerprint else None,
                             "restored_expected_behavior": fingerprint is None})
        except Exception as exc:
            evidence["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            self.runs += 1
        return evidence

    def shrink(self, first: Evaluation) -> tuple[Evaluation, bool]:
        if first.outcome != Outcome.FAIL_SAME or first.fingerprint is None:
            raise ValueError("Shrinking requires a reliably failing evaluation")
        best = first
        reference = first.fingerprint
        stopped = False
        seen: set[str] = {_canonical([m.to_dict() for m in best.mutations])}
        while True:
            improvements: list[Evaluation] = []
            for program in proposals(best.mutations, self.audio.duration, self.audio.rate):
                label = _canonical([m.to_dict() for m in program])
                if label in seen or complexity(program, self.audio.duration) >= complexity(best.mutations, self.audio.duration):
                    continue
                seen.add(label)
                try:
                    evaluation = self.evaluate(program, reference)
                except BudgetExceeded:
                    stopped = True
                    break
                if evaluation.outcome == Outcome.FAIL_SAME:
                    improvements.append(evaluation)
            if improvements:
                best = min(improvements, key=lambda x: complexity(x.mutations, self.audio.duration))
            if stopped or not improvements:
                break
        return best, stopped
