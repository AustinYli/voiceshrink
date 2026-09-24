"""Command-line interface."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from . import __version__
from .artifacts import make_dashboard, make_report, save_investigation, save_regression, verify_integrity, write_integrity
from .audio import Audio, read_wav, write_wav
from .config import write_json
from .engine import BudgetExceeded, Engine, InvalidSeed, NotFailing
from .models import FailureFingerprint, Mutation, Outcome, Scenario
from .search import candidates, temporal_positions
from .targets import create_target
from .validation import validate_scenario


def _affected_duration_reduction(discovery, base_duration: float) -> float:
    from .shrink import complexity
    original = float(complexity(discovery.initial.mutations, base_duration)[2])
    minimal = float(complexity(discovery.minimal.mutations, base_duration)[2])
    return (1 - minimal / original) if original > 0 else 0.0


def _init(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    scenario = directory / "demo-scenario.json"
    audio_path = directory / "demo-order.wav"
    if scenario.exists() or audio_path.exists():
        raise FileExistsError("Demo files already exist; choose an empty directory")
    rate = 16000
    seconds = 3
    time = np.arange(rate * seconds) / rate
    signal = (0.16 * np.sin(2 * np.pi * 220 * time) + 0.06 * np.sin(2 * np.pi * 430 * time)).astype(np.float32)
    write_wav(audio_path, Audio(signal[:, None], rate))
    write_json(scenario, {
        "name": "demo-order-number", "audio": audio_path.name,
        "expected": {"tool": {"name": "lookup_order"}, "arguments": {"order_id": "74821"}},
        "target": {"type": "python", "callable": "voiceshrink.examples:pause_target",
                   "intervention_callable": "voiceshrink.examples:pause_intervention",
                   "fingerprint": "demo-pause-v2"},
        "oracle": {"equivalence": "same_category"},
        "search": {"max_runs": 180, "families": ["silence", "dropout", "noise", "gain"]},
        "evaluation": {"trials": 1, "baseline_trials": 3, "failure_threshold": 1.0},
    })
    print(f"Created {scenario}\nRun: voiceshrink discover {scenario}")
    return 0


def _init_stream(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    scenario = directory / "stream-scenario.json"
    audio_path = directory / "stream-order.wav"
    if scenario.exists() or audio_path.exists():
        raise FileExistsError("Streaming demo files already exist; choose another directory")
    rate = 16000
    time = np.arange(rate * 3) / rate
    signal = (0.16 * np.sin(2 * np.pi * 220 * time) + 0.06 * np.sin(2 * np.pi * 430 * time)).astype(np.float32)
    write_wav(audio_path, Audio(signal[:, None], rate))
    write_json(scenario, {
        "name": "demo-stream-delay", "audio": audio_path.name,
        "expected": {"tool": {"name": "lookup_order"}, "arguments": {"order_id": "74821"}},
        "target": {"type": "python", "callable": "voiceshrink.examples:pause_target",
                   "stream_callable": "voiceshrink.examples:stream_delay_target",
                   "fingerprint": "demo-stream-delay-v1"},
        "oracle": {"equivalence": "same_category"},
        "search": {"max_runs": 180, "families": ["chunk_delay", "jitter", "packet_loss", "reorder"]},
        "evaluation": {"trials": 1, "baseline_trials": 3, "failure_threshold": 1.0},
        "streaming": {"enabled": True, "chunk_ms": 20},
    })
    print(f"Created {scenario}\nRun: voiceshrink discover {scenario}")
    return 0


def _init_investigate(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    scenario = directory / "investigate-scenario.json"
    audio_path = directory / "failed-call.wav"
    if scenario.exists() or audio_path.exists():
        raise FileExistsError("Investigation demo files already exist; choose another directory")
    rate = 16000
    time = np.arange(rate * 4) / rate
    signal = (0.08 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
    signal[int(1.7 * rate):int(2.0 * rate)] += (0.65 * np.sin(2 * np.pi * 700 * time[:int(0.3 * rate)])).astype(np.float32)
    write_wav(audio_path, Audio(signal[:, None], rate))
    write_json(scenario, {
        "name": "demo-production-failure", "audio": audio_path.name,
        "expected": {"tool": {"name": "lookup_order"}, "arguments": {"order_id": "74821"}},
        "target": {"type": "python", "callable": "voiceshrink.examples:marker_failure_target",
                   "fingerprint": "demo-marker-v1"},
        "oracle": {"equivalence": "exact"}, "search": {"max_runs": 180},
        "evaluation": {"trials": 1, "failure_threshold": 1.0},
        "investigate": {"min_window_ms": 250, "boundary_step_ms": 10},
    })
    print(f"Created {scenario}\nRun: voiceshrink investigate {scenario}")
    return 0


def _baseline(path: Path, fresh: bool) -> int:
    scenario = Scenario.load(path)
    with Engine(scenario, fresh=fresh) as engine:
        evaluation = engine.baseline()
        print(f"Baseline PASS | {evaluation.trials}/{evaluation.trials} trials | {engine.runs} target executions")
    return 0


def _discover(path: Path, fresh: bool, json_output: bool = False) -> int:
    scenario = Scenario.load(path)
    with Engine(scenario, fresh=fresh) as engine:
        batch = engine.discover_many()
        if not batch.failures:
            if json_output:
                print(json.dumps({"baseline": batch.baseline.to_dict(), "failures": [], "runs": batch.runs,
                                  "cache_hits": batch.cache_hits, "stopped_by_budget": batch.stopped_by_budget},
                                 ensure_ascii=False))
            else:
                suffix = " (execution budget exhausted)" if batch.stopped_by_budget else ""
                print(f"Baseline PASS | {batch.baseline.trials} trials")
                print(f"No failure found in {batch.runs} target executions{suffix}.")
            return 0
        saved = []
        for discovery in batch.failures:
            bundle = save_regression(scenario, discovery)
            saved.append({"bundle": str(bundle), "report": str(bundle / "report.html"),
                          "evaluation": discovery.minimal.to_dict(), "attribution": discovery.attribution})
        if json_output:
            print(json.dumps({"baseline": batch.baseline.to_dict(), "failures": saved, "runs": batch.runs,
                              "cache_hits": batch.cache_hits, "stopped_by_budget": batch.stopped_by_budget},
                             ensure_ascii=False, default=str))
        else:
            print(f"Baseline PASS | {batch.baseline.trials} trials")
            print(f"Found {len(saved)} distinct failure(s)")
            for index, (discovery, item) in enumerate(zip(batch.failures, saved), 1):
                print(f"Failure {index}:", json.dumps([m.to_dict() for m in discovery.minimal.mutations], ensure_ascii=False))
                print(f"  Reproduction: {discovery.minimal.reproduction_rate:.0%}")
                print(f"  Reduction: {len(discovery.initial.mutations)} -> {len(discovery.minimal.mutations)} mutations; "
                      f"{_affected_duration_reduction(discovery, read_wav(scenario.audio).duration):.1%} affected duration")
                if discovery.attribution and discovery.attribution.get("restored_expected_behavior") is True:
                    print(f"  Intervention: known-good {discovery.attribution['earliest_observed_divergence']} output restored behavior")
                print(f"  Regression: {item['bundle']}")
            print(f"Target executions: {batch.runs}")
            if batch.stopped_by_budget:
                print("Search budget exhausted; further discovery or reduction may be possible.")
    return 0


def _investigate(path: Path, fresh: bool, json_output: bool = False) -> int:
    scenario = Scenario.load(path)
    with Engine(scenario, fresh=fresh) as engine:
        investigation = engine.investigate_failure()
        bundle = save_investigation(scenario, investigation)
        if json_output:
            print(json.dumps({"bundle": str(bundle), "report": str(bundle / "report.html"),
                              "start_seconds": investigation.reduced_start_seconds,
                              "end_seconds": investigation.reduced_end_seconds,
                              "reduction_rate": investigation.reduction_rate,
                              "evaluation": investigation.reduced.to_dict(), "runs": investigation.runs,
                              "stopped_by_budget": investigation.stopped_by_budget}, ensure_ascii=False, default=str))
            return 0
        print("Existing failure reproduced and reduced")
        print(f"Window: {investigation.reduced_start_seconds:.3f}s to {investigation.reduced_end_seconds:.3f}s")
        print(f"Duration: {investigation.original_duration_seconds:.3f}s -> "
              f"{investigation.reduced_end_seconds - investigation.reduced_start_seconds:.3f}s "
              f"({investigation.reduction_rate:.1%} reduction)")
        print(f"Reproduction: {investigation.reduced.reproduction_rate:.0%} | target executions: {investigation.runs}")
        if investigation.stopped_by_budget:
            print("Search budget exhausted; further reduction may be possible.")
        print(f"Investigation: {bundle}\nReport: {bundle / 'report.html'}")
    return 0


def _validate(path: Path, json_output: bool = False) -> int:
    scenario = Scenario.load(path)
    validate_scenario(scenario)
    audio = read_wav(scenario.audio)
    target = create_target(scenario)
    list(candidates(scenario, audio.duration, audio.rate, temporal_positions(audio)))
    if scenario.streaming.get("enabled", False) and not target.can_stream:
        raise ValueError("streaming.enabled requires a stream_callable, stream_command, or stream_url")
    result = {"valid": True, "scenario": scenario.name, "audio": str(scenario.audio),
              "duration_seconds": audio.duration, "sample_rate": audio.rate,
              "target_type": scenario.target.get("type"), "streaming": bool(scenario.streaming.get("enabled", False))}
    print(json.dumps(result, ensure_ascii=False) if json_output else
          f"Valid scenario: {scenario.name} | {audio.duration:.3f}s at {audio.rate} Hz | target={result['target_type']}")
    return 0


def _verify(bundle: Path, json_output: bool = False) -> int:
    bundle = bundle.resolve()
    result = verify_integrity(bundle)
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    elif result["valid"]:
        print(f"Verified {result['checked_files']} files: {bundle}")
    else:
        details = result.get("error") or f"missing={result['missing']} mismatched={result['mismatched']}"
        print(f"Integrity check failed: {details}", file=sys.stderr)
    return 0 if result["valid"] else 1


def _doctor(scenario_path: Path | None = None, json_output: bool = False) -> int:
    checks = {
        "python": {"ok": sys.version_info >= (3, 10), "value": platform.python_version()},
        "numpy": {"ok": importlib.util.find_spec("numpy") is not None,
                  "value": importlib.metadata.version("numpy") if importlib.util.find_spec("numpy") else None},
        "yaml_extra": {"ok": importlib.util.find_spec("yaml") is not None, "optional": True},
        "asr_extra": {"ok": importlib.util.find_spec("faster_whisper") is not None, "optional": True},
        "workspace_writable": {"ok": os.access(Path.cwd(), os.W_OK), "value": str(Path.cwd())},
    }
    errors = []
    scenario_result = None
    if scenario_path:
        try:
            scenario = Scenario.load(scenario_path)
            validate_scenario(scenario)
            audio = read_wav(scenario.audio)
            target = create_target(scenario)
            list(candidates(scenario, audio.duration, audio.rate, temporal_positions(audio)))
            if scenario.streaming.get("enabled", False) and not target.can_stream:
                raise ValueError("streaming is enabled but the target has no streaming adapter")
            if scenario.target.get("type") == "faster-whisper" and not checks["asr_extra"]["ok"]:
                raise ValueError("scenario requires the optional ASR dependency")
            scenario_result = {"ok": True, "name": scenario.name, "target_type": scenario.target.get("type")}
        except Exception as exc:
            scenario_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            errors.append(scenario_result["error"])
    for name, check_result in checks.items():
        if not check_result["ok"] and not check_result.get("optional"):
            errors.append(name)
    result = {"ok": not errors, "checks": checks, "scenario": scenario_result, "errors": errors}
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for name, check_result in checks.items():
            label = "PASS" if check_result["ok"] else ("OPTIONAL" if check_result.get("optional") else "FAIL")
            print(f"{label:8} {name}" + (f" ({check_result['value']})" if check_result.get("value") else ""))
        if scenario_result:
            print(("PASS     " if scenario_result["ok"] else "FAIL     ") + "scenario")
    return 0 if result["ok"] else 1


def _test(root: Path, fresh: bool, json_output: bool = False, junit: Path | None = None) -> int:
    if not root.exists():
        print(json.dumps({"records": [], "summary": {"resolved": 0, "reproducing": 0, "changed": 0, "invalid": 0}})
              if json_output else f"No regression directory: {root}")
        return 0
    bundles = sorted(p for p in root.iterdir() if p.is_dir() and any(
        (p / name).exists() for name in ("integrity.json", "metadata.json", "scenario.json")))
    if not bundles:
        print(json.dumps({"records": [], "summary": {"resolved": 0, "reproducing": 0, "changed": 0, "invalid": 0}})
              if json_output else "No saved regressions.")
        return 0
    resolved = reproduced = other = invalid = 0
    records = []
    replay_cache = root.resolve().parent / "cache"
    for bundle in bundles:
        integrity = verify_integrity(bundle)
        if not integrity["valid"]:
            details = integrity.get("error") or (
                f"missing={integrity.get('missing', [])} mismatched={integrity.get('mismatched', [])}")
            message = f"artifact integrity failed ({details})"
            records.append({"name": bundle.name, "status": "invalid", "message": message,
                            "runtime_seconds": 0.0, "reproduction_rate": None})
            if not json_output:
                print(f"! {bundle.name}: {message}")
            invalid += 1
            continue
        scenario = Scenario.load(bundle / "scenario.json")
        mutations = [Mutation.from_dict(m) for m in json.loads((bundle / "mutation.json").read_text(encoding="utf-8"))["mutations"]]
        fingerprint = FailureFingerprint(**json.loads((bundle / "result.json").read_text(encoding="utf-8"))["fingerprint"])
        with Engine(scenario, cache_dir=replay_cache, fresh=fresh) as engine:
            try:
                engine.baseline()
                evaluation = engine.evaluate(mutations, fingerprint)
            except (InvalidSeed, BudgetExceeded) as exc:
                message = f"invalid baseline or budget ({exc})"
                records.append({"name": bundle.name, "status": "invalid", "message": message,
                                "runtime_seconds": 0.0, "reproduction_rate": None})
                if not json_output:
                    print(f"! {bundle.name}: {message}")
                invalid += 1
                continue
        if evaluation.outcome == Outcome.PASS:
            records.append({"name": bundle.name, "status": "resolved", "message": "failure no longer reproduces",
                            "runtime_seconds": evaluation.runtime_seconds,
                            "reproduction_rate": evaluation.reproduction_rate})
            if not json_output:
                print(f"PASS {bundle.name}: resolved")
            resolved += 1
        elif evaluation.outcome == Outcome.FAIL_SAME:
            records.append({"name": bundle.name, "status": "reproducing", "message": "original failure reproduces",
                            "runtime_seconds": evaluation.runtime_seconds,
                            "reproduction_rate": evaluation.reproduction_rate})
            if not json_output:
                print(f"FAIL {bundle.name}: original failure reproduces ({evaluation.reproduction_rate:.0%})")
            reproduced += 1
        else:
            records.append({"name": bundle.name, "status": "changed", "message": evaluation.outcome.value,
                            "runtime_seconds": evaluation.runtime_seconds,
                            "reproduction_rate": evaluation.reproduction_rate})
            if not json_output:
                print(f"OTHER {bundle.name}: changed behavior ({evaluation.outcome.value})")
            other += 1
    summary = {"resolved": resolved, "reproducing": reproduced, "changed": other, "invalid": invalid}
    if junit:
        suite = ET.Element("testsuite", name="VoiceShrink regressions", tests=str(len(records)),
                           failures=str(reproduced + other + invalid),
                           time=f"{sum(record['runtime_seconds'] for record in records):.6f}")
        for record in records:
            case = ET.SubElement(suite, "testcase", classname="voiceshrink.regression", name=record["name"],
                                 time=f"{record['runtime_seconds']:.6f}")
            if record["status"] != "resolved":
                failure = ET.SubElement(case, "failure", type=record["status"], message=record["message"])
                failure.text = json.dumps(record, ensure_ascii=False)
        junit.parent.mkdir(parents=True, exist_ok=True)
        ET.ElementTree(suite).write(junit, encoding="utf-8", xml_declaration=True)
    if json_output:
        print(json.dumps({"records": records, "summary": summary}, ensure_ascii=False))
    else:
        print(f"{resolved} resolved | {reproduced} reproducing | {other} changed | {invalid} invalid")
    return 1 if reproduced or other or invalid else 0


def _report(issue: str, root: Path) -> int:
    bundle = Path(issue)
    if not bundle.is_dir():
        bundle = root / issue
    if not (bundle / "result.json").is_file():
        raise FileNotFoundError(f"Regression bundle not found: {issue}")
    metadata_path = bundle / "metadata.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("mode") == "investigate":
            report = bundle / "report.html"
            if not report.is_file():
                raise FileNotFoundError(f"Investigation report not found: {report}")
            write_integrity(bundle)
            print(report.resolve())
            return 0
    print(make_report(bundle.resolve()))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may use legacy encodings even when paths contain Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="voiceshrink", description="Discover and minimize voice-system failures")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="Create a runnable local demo")
    p_init.add_argument("directory", nargs="?", type=Path, default=Path.cwd())
    p_stream = sub.add_parser("init-stream", help="Create a runnable streaming-fault demo")
    p_stream.add_argument("directory", nargs="?", type=Path, default=Path.cwd() / "stream-demo")
    p_investigate = sub.add_parser("init-investigate", help="Create an already-failing audio demo")
    p_investigate.add_argument("directory", nargs="?", type=Path, default=Path.cwd() / "investigate-demo")
    for name, help_text in (("baseline", "Verify that the unmodified audio reliably passes"),
                            ("discover", "Search for and minimize target-specific failures")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("scenario", type=Path)
        item.add_argument("--fresh", action="store_true", help="Ignore cached target results")
        if name == "discover":
            item.add_argument("--json", action="store_true", help="Print one machine-readable JSON result")
    p_investigate_run = sub.add_parser("investigate", help="Reduce an already-failing audio input")
    p_investigate_run.add_argument("scenario", type=Path)
    p_investigate_run.add_argument("--fresh", action="store_true", help="Ignore cached target results")
    p_investigate_run.add_argument("--json", action="store_true", help="Print one machine-readable JSON result")
    p_validate = sub.add_parser("validate", help="Validate a scenario without executing its target")
    p_validate.add_argument("scenario", type=Path)
    p_validate.add_argument("--json", action="store_true")
    p_verify = sub.add_parser("verify", help="Verify a saved artifact's hashes")
    p_verify.add_argument("bundle", type=Path)
    p_verify.add_argument("--json", action="store_true")
    p_dashboard = sub.add_parser("dashboard", help="Build an index of saved reports")
    p_dashboard.add_argument("--root", type=Path, default=Path.cwd() / ".voiceshrink")
    p_doctor = sub.add_parser("doctor", help="Check runtime, optional integrations, and a scenario")
    p_doctor.add_argument("--scenario", type=Path)
    p_doctor.add_argument("--json", action="store_true")
    p_test = sub.add_parser("test", help="Rerun saved regressions")
    p_test.add_argument("--root", type=Path, default=Path.cwd() / ".voiceshrink" / "regressions")
    p_test.add_argument("--fresh", action="store_true")
    p_test.add_argument("--json", action="store_true", help="Print one machine-readable JSON result")
    p_test.add_argument("--junit", type=Path, help="Write a JUnit XML report")
    p_report = sub.add_parser("report", help="Regenerate a saved HTML report")
    p_report.add_argument("issue")
    p_report.add_argument("--root", type=Path, default=Path.cwd() / ".voiceshrink" / "regressions")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args.directory)
        if args.command == "init-stream":
            return _init_stream(args.directory)
        if args.command == "init-investigate":
            return _init_investigate(args.directory)
        if args.command == "baseline":
            return _baseline(args.scenario, args.fresh)
        if args.command == "discover":
            return _discover(args.scenario, args.fresh, args.json)
        if args.command == "investigate":
            return _investigate(args.scenario, args.fresh, args.json)
        if args.command == "validate":
            return _validate(args.scenario, args.json)
        if args.command == "verify":
            return _verify(args.bundle, args.json)
        if args.command == "dashboard":
            print(make_dashboard(args.root))
            return 0
        if args.command == "doctor":
            return _doctor(args.scenario, args.json)
        if args.command == "test":
            return _test(args.root, args.fresh, args.json, args.junit)
        if args.command == "report":
            return _report(args.issue, args.root)
    except (FileNotFoundError, FileExistsError, ValueError, InvalidSeed, NotFailing, BudgetExceeded) as exc:
        print(f"VoiceShrink: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
