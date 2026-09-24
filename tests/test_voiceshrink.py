from __future__ import annotations

import json
import io
import math
import sys
import tempfile
import threading
import time
import types
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import numpy as np

from voiceshrink.audio import Audio, read_wav, render, write_wav
from voiceshrink.artifacts import make_dashboard, save_investigation, save_regression, verify_integrity
from voiceshrink.config import write_json
from voiceshrink.cli import _doctor, _test, _validate, main
from voiceshrink.engine import Engine, InvalidSeed, NotFailing
from voiceshrink.integrations import FasterWhisperTarget
from voiceshrink.models import Mutation, Outcome, Scenario, TargetResult
from voiceshrink.oracle import check, same_failure
from voiceshrink.search import candidates, temporal_positions
from voiceshrink.shrink import complexity
from voiceshrink.targets import CommandTarget, HTTPTarget, Target
from voiceshrink.transport import apply_transport, make_stream, transport_summary
from voiceshrink.validation import validate_scenario


class WrongTarget(Target):
    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421"}})


class AlternatingTarget(Target):
    def __init__(self):
        self.count = 0

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        self.count += 1
        order = "7421" if self.count % 5 else "74821"
        return TargetResult({"tool": "lookup_order", "arguments": {"order_id": order}})


class ConcurrentTarget(Target):
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def execute(self, audio_path: Path, scenario: Scenario) -> TargetResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.04)
        with self.lock:
            self.active -= 1
        return TargetResult({"tool": "lookup_order", "arguments": {"order_id": "74821"}})


class VoiceShrinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rate = 8000
        time = np.arange(rate * 3) / rate
        signal = (0.2 * np.sin(2 * math.pi * 220 * time)).astype(np.float32)
        write_wav(self.root / "base.wav", Audio(signal[:, None], rate))

    def tearDown(self):
        self.tmp.cleanup()

    def scenario(self, target="voiceshrink.examples:pause_target", families=None, runs=160):
        path = self.root / "scenario.json"
        write_json(path, {
            "name": "order", "audio": "base.wav",
            "expected": {"tool": {"name": "lookup_order"}, "arguments": {"order_id": "74821"}},
            "target": {"type": "python", "callable": target},
            "search": {"max_runs": runs, "families": families or ["silence"]},
            "evaluation": {"trials": 1, "failure_threshold": 1},
        })
        return Scenario.load(path)

    def test_mutation_identity_and_serialization(self):
        base = read_wav(self.root / "base.wav")
        for mutation in (Mutation("silence", {"position": 1, "duration_ms": 0}),
                         Mutation("gain", {"db": 0}), Mutation("speed", {"rate": 1})):
            changed = render(base, [mutation])
            np.testing.assert_array_equal(changed.samples, base.samples)
            self.assertEqual(Mutation.from_dict(mutation.to_dict()), mutation)
        noisy = render(base, [Mutation("noise", {"snr_db": 12, "seed": 17})])
        again = render(base, [Mutation("noise", {"snr_db": 12, "seed": 17})])
        np.testing.assert_array_equal(noisy.samples, again.samples)
        for mutation in (Mutation("resample", {"target_rate": 4000}),
                         Mutation("codec", {"codec": "mulaw"})):
            changed = render(base, [mutation])
            self.assertEqual(changed.samples.shape, base.samples.shape)
            self.assertTrue(np.isfinite(changed.samples).all())

    def test_all_acoustic_mutations_preserve_audio_contract(self):
        base = read_wav(self.root / "base.wav")
        mutations = (
            Mutation("silence", {"position": 1.0, "duration_ms": 100}),
            Mutation("noise", {"snr_db": 12, "seed": 3}),
            Mutation("gain", {"db": -6}),
            Mutation("dropout", {"start": 0.5, "duration_ms": 100}),
            Mutation("clipping", {"threshold": 0.1}),
            Mutation("speed", {"rate": 0.9}),
            Mutation("bandwidth", {"cutoff_hz": 2000}),
            Mutation("quantize", {"bits": 5}),
            Mutation("resample", {"target_rate": 4000}),
            Mutation("codec", {"codec": "mulaw"}),
            Mutation("crop", {"start": 0.25, "end": 2.75}),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation.kind):
                changed = render(base, [mutation])
                self.assertEqual(changed.rate, base.rate)
                self.assertEqual(changed.samples.ndim, 2)
                self.assertEqual(changed.samples.shape[1], base.samples.shape[1])
                self.assertGreater(len(changed.samples), 0)
                self.assertTrue(np.isfinite(changed.samples).all())
                self.assertLessEqual(float(np.max(changed.samples)), 32767 / 32768)
                self.assertGreaterEqual(float(np.min(changed.samples)), -1)

    def test_oracle_same_failure_modes(self):
        scenario = self.scenario()
        first = check(scenario, TargetResult({"tool": "lookup_order", "arguments": {"order_id": "7421"}}))
        other = check(scenario, TargetResult({"tool": "lookup_order", "arguments": {"order_id": "00000"}}))
        missing = check(scenario, TargetResult(None))
        self.assertEqual(first.category, "wrong_tool_argument")
        self.assertTrue(same_failure(scenario, first, other))
        self.assertFalse(same_failure(scenario, first, missing))
        scenario.oracle = {"equivalence": "exact"}
        self.assertFalse(same_failure(scenario, first, other))

    def test_invalid_seed_stops_before_search(self):
        scenario = self.scenario()
        with Engine(scenario, target=WrongTarget(), cache_dir=self.root / "invalid-cache") as engine:
            with self.assertRaises(InvalidSeed):
                engine.discover()
            self.assertEqual(engine.runs, 1)

    def test_discovers_and_shrinks_pause(self):
        scenario = self.scenario()
        with Engine(scenario, cache_dir=self.root / "cache") as engine:
            discovery = engine.discover()
            self.assertEqual(discovery.initial.outcome, Outcome.FAIL_SAME)
            self.assertEqual(len(discovery.minimal.mutations), 1)
            duration = discovery.minimal.mutations[0].parameters["duration_ms"]
            self.assertGreaterEqual(duration, 499)
            self.assertLessEqual(duration, 510)
            self.assertLess(complexity(discovery.minimal.mutations, engine.audio.duration),
                            complexity(discovery.initial.mutations, engine.audio.duration))
            seeded = engine.evaluate([Mutation("silence", {"position": 1.5, "duration_ms": 800})])
            shrunk, _ = engine.shrink(seeded)
            self.assertLess(complexity(shrunk.mutations, engine.audio.duration),
                            complexity(seeded.mutations, engine.audio.duration))
            self.assertLessEqual(engine.runs, 160)
        with Engine(scenario, cache_dir=self.root / "cache") as cached:
            cached.discover()
            self.assertEqual(cached.runs, 0)
            self.assertGreater(cached.cache_hits, 0)

    def test_region_search_finds_planted_window(self):
        scenario = self.scenario(target="voiceshrink.examples:region_target", families=["dropout"], runs=180)
        with Engine(scenario, cache_dir=self.root / "region-cache") as engine:
            discovery = engine.discover()
            self.assertIsNotNone(discovery.minimal)
            mutation = discovery.minimal.mutations[0]
            self.assertEqual(mutation.kind, "dropout")
            self.assertLess(float(mutation.parameters["start"]), 1.4)
            self.assertGreater(float(mutation.parameters["start"]) + float(mutation.parameters["duration_ms"]) / 1000, 1.2)

    def test_temporal_search_uses_speech_boundaries_and_quiet_gaps(self):
        rate = 8000
        tone = (0.2 * np.sin(2 * math.pi * 220 * np.arange(round(rate * 0.2)) / rate)).astype(np.float32)
        samples = np.concatenate((tone, np.zeros(round(rate * 0.2), dtype=np.float32), tone))
        audio = Audio(samples[:, None], rate)
        positions = temporal_positions(audio)
        self.assertTrue(any(abs(position - 0.2) <= 0.03 for position in positions))
        self.assertTrue(any(abs(position - 0.3) <= 0.03 for position in positions))
        self.assertTrue(any(abs(position - 0.4) <= 0.03 for position in positions))
        scenario = self.scenario(families=["noise"])
        programs = list(candidates(scenario, audio.duration, audio.rate, positions))
        regions = [mutation.parameters for program in programs for mutation in program
                   if mutation.kind == "noise" and "start" in mutation.parameters]
        self.assertTrue(any(params["start"] < 0.3 < params["end"] for params in regions))

    def test_pairwise_search_and_mutation_removal(self):
        scenario = self.scenario(target="voiceshrink.examples:combination_target",
                                 families=["silence", "noise"], runs=200)
        with Engine(scenario, cache_dir=self.root / "combination") as engine:
            discovery = engine.discover()
            self.assertIsNotNone(discovery.minimal)
            self.assertEqual({m.kind for m in discovery.minimal.mutations}, {"silence", "noise"})
        scenario.search["max_pairs"] = 1
        pairs = [program for program in candidates(scenario, 3.0, 8000) if len(program) == 2]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len({mutation.kind for mutation in pairs[0]}), 2)

    def test_seeded_higher_order_combination_search(self):
        scenario = self.scenario(target="voiceshrink.examples:triple_combination_target",
                                 families=["silence", "noise", "speed"], runs=650)
        scenario.search.update({"max_pairs": 0, "max_combinations": 200,
                                "combination_size": 3, "seed": 19})
        first_programs = list(candidates(scenario, 3.0, 8000))
        second_programs = list(candidates(scenario, 3.0, 8000))
        self.assertEqual(first_programs, second_programs)
        wide = self.scenario(families=["silence", "noise", "gain", "clipping", "speed", "codec"])
        wide.search.update({"max_pairs": 0, "max_combinations": 5, "combination_size": 6, "seed": 7})
        higher_order = [program for program in candidates(wide, 3.0, 8000) if len(program) == 6]
        self.assertLessEqual(len(higher_order), 5)
        self.assertTrue(all(len({mutation.kind for mutation in program}) == 6 for program in higher_order))
        with Engine(scenario, cache_dir=self.root / "triple-combination") as engine:
            discovery = engine.discover()
        self.assertIsNotNone(discovery.minimal)
        self.assertEqual({m.kind for m in discovery.minimal.mutations}, {"silence", "noise", "speed"})

    def test_nonmonotonic_shrinking_crosses_passing_region(self):
        scenario = self.scenario(target="voiceshrink.examples:nonmonotonic_target",
                                 families=["silence"], runs=220)
        with Engine(scenario, cache_dir=self.root / "nonmonotonic") as engine:
            discovery = engine.discover()
        initial_ms = float(discovery.initial.mutations[0].parameters["duration_ms"])
        minimal_ms = float(discovery.minimal.mutations[0].parameters["duration_ms"])
        self.assertGreaterEqual(initial_ms, 490)
        self.assertGreaterEqual(minimal_ms, 290)
        self.assertLessEqual(minimal_ms, 291)
        self.assertLess(minimal_ms, 400)

    def test_discovers_multiple_distinct_failures(self):
        scenario = self.scenario(target="voiceshrink.examples:dual_failure_target",
                                 families=["silence", "gain"], runs=320)
        scenario.search["max_failures"] = 2
        with Engine(scenario, cache_dir=self.root / "multi-failure") as engine:
            batch = engine.discover_many()
        self.assertEqual(len(batch.failures), 2)
        fingerprints = {(item.minimal.fingerprint.category, item.minimal.fingerprint.path)
                        for item in batch.failures}
        self.assertEqual(fingerprints, {("wrong_tool_argument", "arguments.order_id"),
                                        ("wrong_tool", "tool")})

    def test_yaml_scenario_subset(self):
        path = self.root / "scenario.yaml"
        path.write_text('name: yaml-order\naudio: base.wav\nexpected:\n  transcript_contains: "74821"\n'
                        'target:\n  type: command\n  command: [python, run.py, "{audio}"]\n'
                        'search:\n  families: [silence, noise]\n', encoding="utf-8")
        scenario = Scenario.load(path)
        self.assertEqual(scenario.search["families"], ["silence", "noise"])
        self.assertEqual(scenario.target["command"], ["python", "run.py", "{audio}"])

    def test_scenario_validation_and_json_output(self):
        scenario = self.scenario()
        output = io.StringIO()
        with redirect_stdout(output):
            code = _validate(scenario.path, json_output=True)
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["sample_rate"], 8000)

    def test_scenario_validation_rejects_invalid_configuration(self):
        cases = (
            (lambda s: s.search.update({"max_runs": 0}), "search.max_runs"),
            (lambda s: s.search.update({"max_concurrency": 1.5}), "search.max_concurrency"),
            (lambda s: s.search.update({"families": ["unknown"]}), "search.families"),
            (lambda s: s.search.update({"max_combinations": -1}), "search.max_combinations"),
            (lambda s: s.search.update({"combination_size": 2}), "search.combination_size"),
            (lambda s: s.evaluation.update({"failure_threshold": 1.1}), "failure_threshold"),
            (lambda s: s.oracle.update({"equivalence": "custom"}), "oracle.callable"),
            (lambda s: s.privacy.update({"cache_results": "false"}), "privacy.cache_results"),
        )
        for mutate, message in cases:
            with self.subTest(message=message):
                scenario = self.scenario()
                mutate(scenario)
                with self.assertRaisesRegex(ValueError, message.replace(".", r"\.")):
                    validate_scenario(scenario)

    def test_cli_version(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("voiceshrink 0.2.6", output.getvalue())

    def test_stochastic_threshold(self):
        scenario = self.scenario()
        scenario.evaluation = {"trials": 5, "failure_threshold": 0.8}
        with Engine(scenario, target=AlternatingTarget(), cache_dir=self.root / "stochastic") as engine:
            evaluation = engine.evaluate([Mutation("gain", {"db": -1})])
            self.assertEqual(evaluation.outcome, Outcome.FAIL_SAME)
            self.assertAlmostEqual(evaluation.reproduction_rate, 0.8)

    def test_repeated_trials_use_configured_concurrency(self):
        scenario = self.scenario()
        scenario.evaluation = {"trials": 4, "failure_threshold": 1}
        scenario.search["max_concurrency"] = 4
        target = ConcurrentTarget()
        with Engine(scenario, target=target, cache_dir=self.root / "concurrency") as engine:
            evaluation = engine.evaluate([])
        self.assertEqual(evaluation.outcome, Outcome.PASS)
        self.assertGreaterEqual(target.max_active, 2)

    def test_transport_mutations_are_deterministic(self):
        base = read_wav(self.root / "base.wav")
        plan = make_stream(base, 20)
        mutations = [Mutation("packet_loss", {"loss_rate": 0.1, "seed": 7}),
                     Mutation("jitter", {"max_jitter_ms": 30, "seed": 7}),
                     Mutation("chunk_delay", {"position": 0.5, "delay_ms": 300}),
                     Mutation("reorder", {"position": 0.2, "distance": 2})]
        first = apply_transport(plan, mutations)
        second = apply_transport(plan, mutations)
        self.assertEqual(first.to_dict(), second.to_dict())
        summary = transport_summary(first)
        self.assertGreater(summary["dropped_chunks"], 0)
        self.assertGreaterEqual(summary["max_delay_ms"], 250)

    def test_streaming_discovery_and_regression_artifact(self):
        scenario = self.scenario(target="voiceshrink.examples:pause_target", families=["chunk_delay"], runs=180)
        scenario.target["stream_callable"] = "voiceshrink.examples:stream_delay_target"
        scenario.streaming = {"enabled": True, "chunk_ms": 20}
        with Engine(scenario, cache_dir=self.root / "stream-cache") as engine:
            discovery = engine.discover()
            self.assertIsNotNone(discovery.minimal)
            self.assertEqual(discovery.minimal.mutations[0].kind, "chunk_delay")
            self.assertGreaterEqual(float(discovery.minimal.mutations[0].parameters["delay_ms"]), 250)
            self.assertLess(float(discovery.minimal.mutations[0].parameters["delay_ms"]), 260)
            bundle = save_regression(scenario, discovery, self.root / "stream-regressions")
        self.assertTrue((bundle / "stream-plan.json").is_file())
        metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(metadata["transport_summary"]["max_delay_ms"], 250)

    def test_investigate_reduces_existing_failure_window(self):
        audio = read_wav(self.root / "base.wav")
        audio.samples[:] *= 0.25
        start, end = int(1.35 * audio.rate), int(1.65 * audio.rate)
        audio.samples[start:end] = 0.8
        write_wav(self.root / "failed.wav", audio)
        path = self.root / "investigate.json"
        write_json(path, {
            "name": "failed-call", "audio": "failed.wav",
            "expected": {"tool": {"name": "lookup_order"}, "arguments": {"order_id": "74821"}},
            "target": {"type": "python", "callable": "voiceshrink.examples:marker_failure_target"},
            "oracle": {"equivalence": "exact"}, "search": {"max_runs": 160},
            "investigate": {"min_window_ms": 250, "boundary_step_ms": 10},
        })
        scenario = Scenario.load(path)
        with Engine(scenario, cache_dir=self.root / "investigate-cache") as engine:
            investigation = engine.investigate_failure()
            bundle = save_investigation(scenario, investigation, self.root / "investigations")
        self.assertGreater(investigation.reduction_rate, 0.8)
        self.assertLessEqual(investigation.reduced_end_seconds - investigation.reduced_start_seconds, 0.27)
        self.assertTrue((bundle / "report.html").is_file())
        self.assertTrue((bundle / "reduced.wav").is_file())

    def test_investigate_rejects_passing_input(self):
        scenario = self.scenario()
        with Engine(scenario, cache_dir=self.root / "passing-investigate") as engine:
            with self.assertRaises(NotFailing):
                engine.investigate_failure()

    def test_command_adapter(self):
        scenario = self.scenario()
        script = self.root / "target.py"
        script.write_text('import json; print(json.dumps({"tool":"lookup_order","arguments":{"order_id":"74821"}}))\n', encoding="utf-8")
        target = CommandTarget([sys.executable, str(script), "{audio}"], self.root)
        result = target.execute(self.root / "base.wav", scenario)
        self.assertIsNone(check(scenario, result))

    def test_command_timeout_is_explicit(self):
        scenario = self.scenario()
        script = self.root / "slow-target.py"
        script.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
        target = CommandTarget([sys.executable, str(script)], self.root, timeout=0.01)
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            target.execute(self.root / "base.wav", scenario)

    def test_invalid_wav_errors_are_normalized(self):
        invalid = self.root / "invalid.wav"
        invalid.write_bytes(b"not a wave file")
        with self.assertRaisesRegex(ValueError, "Invalid WAV file"):
            read_wav(invalid)
        with self.assertRaisesRegex(ValueError, "Invalid audio samples"):
            write_wav(self.root / "bad-rate.wav", Audio(np.zeros((8, 1), dtype=np.float32), 0))

    def test_command_environment_from(self):
        scenario = self.scenario()
        script = self.root / "env-target.py"
        script.write_text(
            'import json,os\nprint(json.dumps({"tool":"lookup_order","arguments":{"order_id":os.environ["ORDER_FOR_TARGET"]}}))\n',
            encoding="utf-8")
        target = CommandTarget([sys.executable, str(script)], self.root,
                               environment_from={"ORDER_FOR_TARGET": "VOICE_TEST_ORDER"})
        with patch.dict("os.environ", {"VOICE_TEST_ORDER": "74821"}):
            result = target.execute(self.root / "base.wav", scenario)
        self.assertIsNone(check(scenario, result))

    def test_regression_bundle_and_replay(self):
        scenario = self.scenario()
        scenario.target["download_root"] = ".models"
        with Engine(scenario, cache_dir=self.root / "source-cache") as engine:
            discovery = engine.discover()
            bundle = save_regression(scenario, discovery, self.root / "regressions")
        self.assertTrue((bundle / "report.html").is_file())
        self.assertTrue((bundle / "integrity.json").is_file())
        self.assertTrue(verify_integrity(bundle)["valid"])
        report = (bundle / "report.html").read_text(encoding="utf-8")
        self.assertIn("Minimized failure", report)
        self.assertIn("Affected duration reduced", report)
        metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["original_mutation_count"], 1)
        self.assertEqual(metadata["minimal_mutation_count"], 1)
        self.assertGreater(metadata["affected_duration_reduction_rate"], 0)
        self.assertGreater(metadata["failure_rate_increase"], 0)
        self.assertEqual(len(metadata["target_config_sha256"]), 64)
        self.assertEqual(len(metadata["minimal_complexity"]), 5)
        saved_data = json.loads((bundle / "scenario.json").read_text(encoding="utf-8"))
        self.assertTrue(Path(saved_data["target"]["download_root"]).is_absolute())
        saved = Scenario.load(bundle / "scenario.json")
        mutations = [Mutation.from_dict(m) for m in json.loads((bundle / "mutation.json").read_text())["mutations"]]
        with Engine(saved, cache_dir=self.root / "replay-cache") as engine:
            engine.baseline()
            replay = engine.evaluate(mutations, discovery.minimal.fingerprint)
        self.assertEqual(replay.outcome, Outcome.FAIL_SAME)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(_test(bundle.parent, fresh=False), 1)
        junit = self.root / "results" / "regressions.xml"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(_test(bundle.parent, fresh=False, junit=junit), 1)
        self.assertIn("<testsuite", junit.read_text(encoding="utf-8"))
        self.assertIn('type="reproducing"', junit.read_text(encoding="utf-8"))
        self.assertFalse((bundle / ".voiceshrink").exists())
        dashboard = make_dashboard(self.root)
        self.assertTrue(dashboard.is_file())
        self.assertIn("order", dashboard.read_text(encoding="utf-8"))
        dashboard_data = json.loads((self.root / "dashboard.json").read_text(encoding="utf-8"))
        self.assertGreater(dashboard_data["entries"][0]["reduction_rate"], 0)
        self.assertTrue(dashboard_data["entries"][0]["integrity_valid"])

    def test_integrity_detects_artifact_changes(self):
        scenario = self.scenario()
        with Engine(scenario, cache_dir=self.root / "integrity-cache") as engine:
            discovery = engine.discover()
            bundle = save_regression(scenario, discovery, self.root / "integrity-regressions")
        (bundle / "result.json").write_text("{}\n", encoding="utf-8")
        result = verify_integrity(bundle)
        self.assertFalse(result["valid"])
        self.assertEqual(result["mismatched"], ["result.json"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(_test(bundle.parent, fresh=False, json_output=True), 1)
        regression_result = json.loads(output.getvalue())
        self.assertEqual(regression_result["summary"]["invalid"], 1)
        self.assertIn("integrity", regression_result["records"][0]["message"])

    def test_regression_suite_reports_missing_files_and_malformed_manifest(self):
        scenario = self.scenario()
        with Engine(scenario, cache_dir=self.root / "damaged-cache") as engine:
            discovery = engine.discover()
            missing_bundle = save_regression(scenario, discovery, self.root / "damaged-regressions")
            malformed_bundle = save_regression(scenario, discovery, self.root / "damaged-regressions")
        (missing_bundle / "scenario.json").unlink()
        (malformed_bundle / "integrity.json").write_text("{bad json", encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(_test(missing_bundle.parent, fresh=False, json_output=True), 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["summary"]["invalid"], 2)
        self.assertTrue(all("integrity" in record["message"] for record in payload["records"]))
        self.assertFalse(verify_integrity(malformed_bundle)["valid"])

    def test_report_privacy_redacts_outputs_and_audio(self):
        scenario = self.scenario()
        scenario.privacy = {"redact_report_outputs": True, "hide_report_audio": True}
        with Engine(scenario, cache_dir=self.root / "privacy-cache") as engine:
            discovery = engine.discover()
            bundle = save_regression(scenario, discovery, self.root / "privacy-regressions")
        report = (bundle / "report.html").read_text(encoding="utf-8")
        self.assertIn("Audio hidden by scenario privacy settings", report)
        self.assertIn("[redacted]", report)
        self.assertNotIn("74821", report)
        self.assertNotIn("<audio", report)

    def test_sensitive_scenario_can_disable_result_cache(self):
        scenario = self.scenario()
        scenario.privacy = {"cache_results": False}
        cache_dir = self.root / "no-results-cache"
        with Engine(scenario, cache_dir=cache_dir) as engine:
            first = engine.evaluate([])
            second = engine.evaluate([])
            row_count = engine.db.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
        self.assertEqual(first.outcome, Outcome.PASS)
        self.assertEqual(second.outcome, Outcome.PASS)
        self.assertEqual(row_count, 0)

    def test_intervention_strengthens_stage_attribution(self):
        scenario = self.scenario()
        scenario.target["intervention_callable"] = "voiceshrink.examples:pause_intervention"
        with Engine(scenario, cache_dir=self.root / "intervention") as engine:
            discovery = engine.discover()
        self.assertEqual(discovery.attribution["earliest_observed_divergence"], "asr")
        self.assertTrue(discovery.attribution["intervention_supported"])
        self.assertTrue(discovery.attribution["restored_expected_behavior"])
        self.assertIsNone(discovery.attribution["failure_fingerprint"])

    def test_http_adapter_payload(self):
        scenario = self.scenario()

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        def fake_urlopen(request, timeout):
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Content-type"), "audio/wav")
            self.assertEqual(request.data, (self.root / "base.wav").read_bytes())
            return Response(b'{"tool":"lookup_order","arguments":{"order_id":"74821"}}')

        with patch("voiceshrink.targets.urlopen", side_effect=fake_urlopen):
            result = HTTPTarget("http://example.invalid/test").execute(self.root / "base.wav", scenario)
        self.assertIsNone(check(scenario, result))

    def test_http_adapter_real_local_roundtrip(self):
        scenario = self.scenario()
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["body"] = self.rfile.read(length)
                received["scenario"] = self.headers.get("X-VoiceShrink-Scenario")
                payload = json.dumps({"tool": "lookup_order",
                                      "arguments": {"order_id": "74821"}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            target = HTTPTarget(f"http://127.0.0.1:{server.server_port}/voice", timeout=2)
            result = target.execute(self.root / "base.wav", scenario)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertTrue(received["body"].startswith(b"RIFF"))
        self.assertEqual(received["scenario"], scenario.name)
        self.assertIsNone(check(scenario, result))

    def test_http_streaming_protocol(self):
        scenario = self.scenario()

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data)
            self.assertEqual(request.full_url, "http://example.invalid/stream")
            self.assertGreater(len(payload["chunks"]), 1)
            return Response(b'{"tool":"lookup_order","arguments":{"order_id":"74821"}}')

        target = HTTPTarget("http://example.invalid/file", stream_url="http://example.invalid/stream")
        with patch("voiceshrink.targets.urlopen", side_effect=fake_urlopen):
            result = target.execute_stream(make_stream(read_wav(self.root / "base.wav")), scenario)
        self.assertIsNone(check(scenario, result))

    def test_http_headers_from_environment(self):
        scenario = self.scenario()

        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *_args): self.close()

        def fake_urlopen(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer secret")
            return Response(b'{"tool":"lookup_order","arguments":{"order_id":"74821"}}')

        target = HTTPTarget("http://example.invalid/file", headers_env={"Authorization": "VOICE_API_AUTH"})
        with patch.dict("os.environ", {"VOICE_API_AUTH": "Bearer secret"}), \
             patch("voiceshrink.targets.urlopen", side_effect=fake_urlopen):
            result = target.execute(self.root / "base.wav", scenario)
        self.assertIsNone(check(scenario, result))

    def test_optional_faster_whisper_adapter_contract(self):
        scenario = self.scenario()

        class FakeModel:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs

            def transcribe(self, path, **kwargs):
                return [types.SimpleNamespace(text=" seven four "), types.SimpleNamespace(text=" eight two one ")], \
                    types.SimpleNamespace(language="en")

        module = types.SimpleNamespace(WhisperModel=FakeModel)
        target = FasterWhisperTarget({"model": "test-model", "device": "cpu", "compute_type": "int8"})
        with patch.dict(sys.modules, {"faster_whisper": module}):
            result = target.execute(self.root / "base.wav", scenario)
        self.assertEqual(result.final_output["transcript"], "seven four eight two one")
        self.assertEqual(result.stages[0].name, "asr")
        self.assertEqual(result.metadata["language"], "en")

    def test_doctor_checks_core_and_scenario(self):
        scenario = self.scenario()
        output = io.StringIO()
        with redirect_stdout(output):
            code = _doctor(scenario.path, json_output=True)
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["scenario"]["ok"])

    def test_command_intervention_protocol(self):
        scenario = self.scenario()
        script = self.root / "intervene.py"
        script.write_text(
            'import json,sys\np=json.load(sys.stdin)\norder="74821" if "74821" in p["replacement"] else "7421"\n'
            'print(json.dumps({"tool":"lookup_order","arguments":{"order_id":order}}))\n', encoding="utf-8")
        target = CommandTarget([sys.executable, str(script), "{audio}"], self.root,
                               intervention_command=[sys.executable, str(script), "{audio}"])
        self.assertTrue(target.can_intervene)
        result = target.intervene(self.root / "base.wav", scenario, "asr", "order 74821")
        self.assertIsNone(check(scenario, result))

    def test_command_streaming_protocol(self):
        scenario = self.scenario()
        script = self.root / "stream.py"
        script.write_text(
            'import json,sys\np=json.load(sys.stdin)\n'
            'print(json.dumps({"tool":"lookup_order","arguments":{"order_id":"74821"},"chunks":len(p["chunks"])}))\n',
            encoding="utf-8")
        target = CommandTarget([sys.executable, str(script)], self.root,
                               stream_command=[sys.executable, str(script)])
        self.assertTrue(target.can_stream)
        result = target.execute_stream(make_stream(read_wav(self.root / "base.wav")), scenario)
        self.assertIsNone(check(scenario, result))


if __name__ == "__main__":
    unittest.main()
