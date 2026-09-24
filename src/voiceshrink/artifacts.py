"""Portable regression bundles and static HTML reports."""

from __future__ import annotations

import hashlib
import html
import json
import platform
import re
import shutil
import sys
from pathlib import Path

import numpy as np

from . import __version__
from .audio import read_wav, render, write_wav
from .config import write_json
from .engine import Discovery, Investigation
from .models import Mutation, Scenario
from .oracle import earliest_divergence
from .shrink import complexity
from .transport import TRANSPORT_KINDS, apply_transport, make_stream, transport_summary


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "scenario"


def write_integrity(bundle: Path) -> Path:
    files = {}
    for path in sorted(item for item in bundle.iterdir() if item.is_file() and item.name != "integrity.json"):
        files[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size}
    output = bundle / "integrity.json"
    write_json(output, {"algorithm": "sha256", "files": files})
    return output


def verify_integrity(bundle: Path) -> dict:
    bundle = bundle.resolve()
    manifest_path = bundle / "integrity.json"
    if not manifest_path.is_file():
        return {"valid": False, "bundle": str(bundle), "error": "integrity.json is missing",
                "missing": [], "mismatched": []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"valid": False, "bundle": str(bundle),
                "error": f"integrity.json is invalid: {type(exc).__name__}",
                "missing": [], "mismatched": []}
    if not isinstance(manifest, dict) or manifest.get("algorithm") != "sha256" or not isinstance(manifest.get("files"), dict):
        return {"valid": False, "bundle": str(bundle),
                "error": "integrity.json must declare sha256 and a files mapping",
                "missing": [], "mismatched": []}
    missing, mismatched, unsafe = [], [], []
    for name, expected in manifest["files"].items():
        if not isinstance(name, str) or not isinstance(expected, dict):
            unsafe.append(str(name))
            continue
        path = (bundle / name).resolve()
        if path.parent != bundle:
            unsafe.append(name)
            continue
        if not path.is_file():
            missing.append(name)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected.get("sha256") or path.stat().st_size != expected.get("size"):
            mismatched.append(name)
    return {"valid": not missing and not mismatched and not unsafe, "bundle": str(bundle),
            "missing": missing, "mismatched": mismatched, "unsafe": unsafe,
            "checked_files": len(manifest["files"])}


def _portable_target(scenario: Scenario) -> dict:
    target = {**scenario.target, "working_dir": str(scenario.path.parent)}
    if target.get("download_root"):
        root = Path(target["download_root"])
        if not root.is_absolute():
            target["download_root"] = str((scenario.path.parent / root).resolve())
    return target


def _discovery_metrics(scenario: Scenario, discovery: Discovery) -> dict:
    """Summarize reduction and provenance without making a global-minimum claim."""
    base_duration = read_wav(scenario.audio).duration
    original = complexity(discovery.initial.mutations, base_duration)
    minimal = complexity(discovery.minimal.mutations, base_duration)
    original_duration = float(original[2])
    minimal_duration = float(minimal[2])
    duration_reduction = (1 - minimal_duration / original_duration) if original_duration > 0 else 0.0
    seeds = sorted({int(m.parameters["seed"]) for m in discovery.minimal.mutations
                    if "seed" in m.parameters})
    target_config = _portable_target(scenario)
    return {
        "original_mutation_count": len(discovery.initial.mutations),
        "minimal_mutation_count": len(discovery.minimal.mutations),
        "original_affected_duration_seconds": original_duration,
        "minimal_affected_duration_seconds": minimal_duration,
        "affected_duration_reduction_rate": duration_reduction,
        "original_complexity": list(original),
        "minimal_complexity": list(minimal),
        "baseline_failure_rate": discovery.baseline.reproduction_rate,
        "minimized_failure_rate": discovery.minimal.reproduction_rate,
        "failure_rate_increase": discovery.minimal.reproduction_rate - discovery.baseline.reproduction_rate,
        "target_config_sha256": hashlib.sha256(
            json.dumps(target_config, sort_keys=True, default=str).encode()).hexdigest(),
        "mutation_seeds": seeds,
        "prompt_hash": scenario.target.get("prompt_hash"),
        "tool_schema_hash": scenario.target.get("tool_schema_hash"),
        "models": scenario.target.get("models"),
        "target_result_metadata": {
            "baseline": discovery.baseline.result.metadata if discovery.baseline.result else {},
            "failure": discovery.minimal.result.metadata if discovery.minimal.result else {},
        },
    }


def save_regression(scenario: Scenario, discovery: Discovery, root: Path | None = None) -> Path:
    if discovery.minimal is None or discovery.initial is None:
        raise ValueError("No discovered failure to save")
    root = root or scenario.path.parent / ".voiceshrink" / "regressions"
    root.mkdir(parents=True, exist_ok=True)
    prefix = _safe_name(scenario.name)
    number = 1
    while (root / f"{prefix}-{number:03d}").exists():
        number += 1
    bundle = root / f"{prefix}-{number:03d}"
    bundle.mkdir()
    shutil.copyfile(scenario.audio, bundle / "base.wav")
    acoustic = [mutation for mutation in discovery.minimal.mutations if mutation.kind not in TRANSPORT_KINDS]
    transport = [mutation for mutation in discovery.minimal.mutations if mutation.kind in TRANSPORT_KINDS]
    rendered = render(read_wav(scenario.audio), acoustic)
    write_wav(bundle / "minimal.wav", rendered)
    stream_stats = None
    if transport or scenario.streaming.get("enabled", False):
        stream = apply_transport(make_stream(rendered, float(scenario.streaming.get("chunk_ms", 20))), transport)
        write_json(bundle / "stream-plan.json", stream.to_dict())
        stream_stats = transport_summary(stream)
    saved_scenario = scenario.to_dict(audio="base.wav")
    saved_scenario["target"] = _portable_target(scenario)
    write_json(bundle / "scenario.json", saved_scenario)
    write_json(bundle / "mutation.json", {"mutations": [m.to_dict() for m in discovery.minimal.mutations]})
    write_json(bundle / "baseline.json", discovery.baseline.to_dict())
    write_json(bundle / "result.json", discovery.minimal.to_dict())
    stage = earliest_divergence(discovery.baseline.result, discovery.minimal.result)
    write_json(bundle / "metadata.json", {
        "voiceshrink_version": __version__, "python": sys.version.split()[0], "os": platform.platform(),
        "base_audio_sha256": hashlib.sha256(scenario.audio.read_bytes()).hexdigest(),
        "scenario_sha256": hashlib.sha256(json.dumps(saved_scenario, sort_keys=True).encode()).hexdigest(),
        "target_fingerprint": scenario.target.get("fingerprint"),
        "original_mutations": [m.to_dict() for m in discovery.initial.mutations],
        "minimal_mutations": [m.to_dict() for m in discovery.minimal.mutations],
        "target_executions": discovery.runs, "cache_hits": discovery.cache_hits,
        "search_duration_seconds": discovery.duration_seconds,
        "estimated_target_cost": discovery.runs * float(scenario.search.get("cost_per_run", 0)),
        "cost_currency": scenario.search.get("cost_currency", "USD"),
        "search_budget_exhausted": discovery.stopped_by_budget,
        "reproduction_rate": discovery.minimal.reproduction_rate,
        "earliest_observed_divergence": stage,
        "interventional_attribution": discovery.attribution,
        "transport_summary": stream_stats,
        **_discovery_metrics(scenario, discovery),
    })
    make_report(bundle)
    return bundle


def _waveform(path: Path, region: tuple[float, float] | None = None) -> str:
    audio = read_wav(path)
    mono = audio.samples.mean(axis=1)
    width, height = 700, 108
    bins = min(width, len(mono))
    edges = np.linspace(0, len(mono), bins + 1, dtype=int)
    bars = []
    for index in range(bins):
        segment = mono[edges[index]:max(edges[index] + 1, edges[index + 1])]
        amplitude = min(1, float(np.max(np.abs(segment))))
        y = (height / 2) * (1 - amplitude)
        bars.append(f'<line x1="{index}" x2="{index}" y1="{y:.1f}" y2="{height-y:.1f}"/>')
    highlight = ""
    if region and audio.duration:
        left = width * region[0] / audio.duration
        right = width * region[1] / audio.duration
        highlight = f'<rect x="{left:.1f}" width="{max(1,right-left):.1f}" height="{height}" fill="#f59e0b" opacity=".22"/>'
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Audio waveform">{highlight}<g stroke="#3161ad" stroke-width="1">{"".join(bars)}</g></svg>'


def make_report(bundle: Path) -> Path:
    baseline = json.loads((bundle / "baseline.json").read_text(encoding="utf-8"))
    result = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
    metadata = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
    scenario = json.loads((bundle / "scenario.json").read_text(encoding="utf-8"))
    privacy = scenario.get("privacy") or {}
    redact = bool(privacy.get("redact_report_outputs", False))
    hide_audio = bool(privacy.get("hide_report_audio", False))
    mutations = metadata["minimal_mutations"]
    region = None
    if len(mutations) == 1:
        m = mutations[0]
        if m["type"] == "silence":
            region = (float(m["position"]), float(m["position"]) + float(m["duration_ms"]) / 1000)
        elif m["type"] == "dropout":
            region = (float(m.get("start", 0)), float(m.get("start", 0)) + float(m.get("duration_ms", 0)) / 1000)
        elif "start" in m and "end" in m:
            region = (float(m["start"]), float(m["end"]))
    escape = lambda value: html.escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    stage = metadata.get("earliest_observed_divergence")
    attribution = f"Earliest observed divergence: {html.escape(stage)}" if stage else "Stage attribution unavailable"
    intervention = metadata.get("interventional_attribution") or {}
    if intervention.get("restored_expected_behavior") is True:
        attribution += ". Replacing that stage with its known-good output restored expected behavior."
    elif intervention.get("intervention_supported") and intervention.get("error"):
        attribution += f". Intervention was inconclusive: {html.escape(intervention['error'])}."
    transport = metadata.get("transport_summary")
    transport_html = f'<h2>Transport result</h2><pre>{escape(transport)}</pre>' if transport else ""
    original_count = metadata.get("original_mutation_count", len(metadata.get("original_mutations", [])))
    minimal_count = metadata.get("minimal_mutation_count", len(mutations))
    affected_reduction = metadata.get("affected_duration_reduction_rate")
    reduction_text = (f"Affected duration reduced {float(affected_reduction):.1%}"
                      if affected_reduction is not None else "Affected-duration reduction unavailable")
    base_media = '<p>Audio hidden by scenario privacy settings.</p>' if hide_audio else f'{_waveform(bundle / "base.wav")}<audio controls src="base.wav"></audio>'
    failure_media = '<p>Audio hidden by scenario privacy settings.</p>' if hide_audio else f'{_waveform(bundle / "minimal.wav", region)}<audio controls src="minimal.wav"></audio>'
    baseline_output = "[redacted]" if redact else baseline.get("result", {}).get("final_output")
    failure_output = "[redacted]" if redact else result.get("result", {}).get("final_output")
    expected_output = "[redacted]" if redact else scenario["expected"]
    fingerprint_output = "[redacted]" if redact else result.get("fingerprint")
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoiceShrink report · {html.escape(scenario["name"])}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f7fb;color:#172235}}header{{background:#122847;color:white;padding:34px max(24px,calc((100% - 1120px)/2))}}main{{max-width:1120px;margin:auto;padding:24px}}h1{{margin:0}}.sub{{opacity:.8}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}}section{{background:white;border:1px solid #dce5ef;border-radius:14px;padding:20px;margin-bottom:18px}}svg{{width:100%;background:#f7f9fc;border-radius:8px}}audio{{width:100%;margin:12px 0}}pre{{white-space:pre-wrap;word-break:break-word;background:#f2f5f9;padding:14px;border-radius:8px}}.pill{{display:inline-block;background:#d9ebff;border-radius:100px;padding:5px 12px}}.mut{{font-family:ui-monospace,monospace}}</style></head>
<body><header><h1>VoiceShrink</h1><div class="sub">{html.escape(scenario["name"])} · smallest observed reproducing mutation</div></header><main>
<section><span class="pill">Reproduction {metadata["reproduction_rate"]:.0%}</span> <span class="pill">{metadata["target_executions"]} target executions</span> <span class="pill">{original_count} → {minimal_count} mutations</span><p>{html.escape(reduction_text)}. {attribution}</p></section>
<div class="grid"><section><h2>Known-good audio</h2>{base_media}<h3>Actual output</h3><pre>{escape(baseline_output)}</pre></section>
<section><h2>Minimized failure</h2>{failure_media}<h3>Actual output</h3><pre>{escape(failure_output)}</pre></section></div>
<section><h2>Expected behavior</h2><pre>{escape(expected_output)}</pre><h2>Failure</h2><pre>{escape(fingerprint_output)}</pre></section>
<section><h2>Mutation program</h2><pre class="mut">{escape(mutations)}</pre>{transport_html}<p>Test the saved case with <code>voiceshrink test</code>.</p></section>
</main></body></html>'''
    output = bundle / "report.html"
    output.write_text(page, encoding="utf-8")
    write_integrity(bundle)
    return output


def save_investigation(scenario: Scenario, investigation: Investigation, root: Path | None = None) -> Path:
    root = root or scenario.path.parent / ".voiceshrink" / "investigations"
    root.mkdir(parents=True, exist_ok=True)
    prefix = _safe_name(scenario.name)
    number = 1
    while (root / f"{prefix}-{number:03d}").exists():
        number += 1
    bundle = root / f"{prefix}-{number:03d}"
    bundle.mkdir()
    shutil.copyfile(scenario.audio, bundle / "original.wav")
    mutation = Mutation("crop", {"start": investigation.reduced_start_seconds,
                                  "end": investigation.reduced_end_seconds})
    reduced_audio = render(read_wav(scenario.audio), [mutation])
    write_wav(bundle / "reduced.wav", reduced_audio)
    saved_scenario = scenario.to_dict(audio="original.wav")
    saved_scenario["target"] = _portable_target(scenario)
    write_json(bundle / "scenario.json", saved_scenario)
    write_json(bundle / "mutation.json", {"mutations": [mutation.to_dict()]})
    write_json(bundle / "result.json", investigation.reduced.to_dict())
    metadata = {
        "voiceshrink_version": __version__, "mode": "investigate",
        "original_duration_seconds": investigation.original_duration_seconds,
        "reduced_start_seconds": investigation.reduced_start_seconds,
        "reduced_end_seconds": investigation.reduced_end_seconds,
        "reduced_duration_seconds": investigation.reduced_end_seconds - investigation.reduced_start_seconds,
        "reduction_rate": investigation.reduction_rate,
        "reproduction_rate": investigation.reduced.reproduction_rate,
        "target_executions": investigation.runs, "cache_hits": investigation.cache_hits,
        "search_duration_seconds": investigation.duration_seconds,
        "estimated_target_cost": investigation.runs * float(scenario.search.get("cost_per_run", 0)),
        "cost_currency": scenario.search.get("cost_currency", "USD"),
        "search_budget_exhausted": investigation.stopped_by_budget,
        "claim": "Smallest observed contiguous failing window under the configured search budget",
    }
    write_json(bundle / "metadata.json", metadata)
    escape = lambda value: html.escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    redact = bool(scenario.privacy.get("redact_report_outputs", False))
    hide_audio = bool(scenario.privacy.get("hide_report_audio", False))
    original_media = '<p>Audio hidden by scenario privacy settings.</p>' if hide_audio else f'{_waveform(bundle / "original.wav")}<audio controls src="original.wav"></audio>'
    reduced_media = '<p>Audio hidden by scenario privacy settings.</p>' if hide_audio else f'{_waveform(bundle / "reduced.wav")}<audio controls src="reduced.wav"></audio>'
    fingerprint = "[redacted]" if redact else investigation.reduced.to_dict().get("fingerprint")
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VoiceShrink investigation · {html.escape(scenario.name)}</title><style>body{{font:16px/1.5 system-ui;background:#f4f7fb;color:#172235;margin:0}}header{{background:#36235c;color:white;padding:32px max(24px,calc((100% - 1100px)/2))}}main{{max-width:1100px;margin:auto;padding:24px}}section{{background:white;border:1px solid #ddd7e8;border-radius:14px;padding:20px;margin-bottom:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}}svg,audio{{width:100%}}pre{{white-space:pre-wrap;background:#f4f1f8;padding:14px;border-radius:8px}}.pill{{background:#eadffd;border-radius:99px;padding:5px 12px}}</style></head>
<body><header><h1>Failure investigation</h1><p>{html.escape(scenario.name)}</p></header><main>
<section><span class="pill">Reduced {investigation.reduction_rate:.1%}</span> <span class="pill">Reproduction {investigation.reduced.reproduction_rate:.0%}</span><p>{metadata['claim']}.</p><p>This mode starts from failing audio without a known-good reference. The retained window is evidence about reproduction, not proof that discarded audio is semantically irrelevant or that the window is a root cause.</p></section>
<div class="grid"><section><h2>Original failure</h2>{original_media}<p>{investigation.original_duration_seconds:.3f} seconds</p></section>
<section><h2>Reduced failure</h2>{reduced_media}<p>{metadata['reduced_duration_seconds']:.3f} seconds, from {investigation.reduced_start_seconds:.3f} to {investigation.reduced_end_seconds:.3f}</p></section></div>
<section><h2>Failure fingerprint</h2><pre>{escape(fingerprint)}</pre><h2>Crop program</h2><pre>{escape(mutation.to_dict())}</pre></section>
</main></body></html>'''
    (bundle / "report.html").write_text(page, encoding="utf-8")
    write_integrity(bundle)
    return bundle


def make_dashboard(root: Path) -> Path:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for mode, directory in (("regression", root / "regressions"), ("investigation", root / "investigations")):
        if not directory.is_dir():
            continue
        for bundle in sorted(path for path in directory.iterdir() if path.is_dir()):
            metadata_path, scenario_path = bundle / "metadata.json", bundle / "scenario.json"
            if not metadata_path.is_file() or not scenario_path.is_file():
                continue
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            mutations_path = bundle / "mutation.json"
            mutations = json.loads(mutations_path.read_text(encoding="utf-8")) if mutations_path.is_file() else {}
            integrity = verify_integrity(bundle)
            entries.append({"mode": mode, "name": bundle.name, "scenario": scenario.get("name", bundle.name),
                            "artifact_version": metadata.get("voiceshrink_version"),
                            "reproduction_rate": metadata.get("reproduction_rate"),
                            "target_executions": metadata.get("target_executions"),
                            "search_duration_seconds": metadata.get("search_duration_seconds"),
                            "search_budget_exhausted": metadata.get("search_budget_exhausted", False),
                            "estimated_target_cost": metadata.get("estimated_target_cost"),
                            "cost_currency": metadata.get("cost_currency"),
                            "reduction_rate": metadata.get("affected_duration_reduction_rate",
                                                           metadata.get("reduction_rate")),
                            "original_mutation_count": metadata.get("original_mutation_count"),
                            "minimal_mutation_count": metadata.get("minimal_mutation_count"),
                            "mutations": mutations.get("mutations", []), "integrity_valid": integrity["valid"],
                            "integrity": integrity,
                            "report": (bundle.relative_to(root) / "report.html").as_posix()})
    write_json(root / "dashboard.json", {"voiceshrink_version": __version__, "entries": entries})
    cards = []
    for entry in entries:
        reproduction = entry["reproduction_rate"]
        rate = f"{float(reproduction):.0%}" if reproduction is not None else "unknown"
        integrity = "verified" if entry["integrity_valid"] else "unverified"
        reduction = entry.get("reduction_rate")
        reduction_text = f"{float(reduction):.1%} reduction" if reduction is not None else "reduction unavailable"
        cost = entry.get("estimated_target_cost")
        cost_text = (f"{float(cost):.4f} {entry.get('cost_currency') or 'USD'}" if cost is not None
                     else "cost unavailable")
        version = entry.get("artifact_version") or "unknown version"
        cards.append(f'''<article><div class="kind">{html.escape(entry['mode'])}</div><h2>{html.escape(entry['scenario'])}</h2>
<p><strong>{rate}</strong> reproduction · {html.escape(integrity)} · {html.escape(reduction_text)}</p>
<p>{entry.get('target_executions') or 0} target executions · {html.escape(cost_text)} · VoiceShrink {html.escape(version)}</p>
<pre>{html.escape(json.dumps(entry['mutations'], ensure_ascii=False, indent=2))}</pre>
<a href="{html.escape(entry['report'])}">Open report</a></article>''')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VoiceShrink dashboard</title><style>body{{font:16px/1.5 system-ui;margin:0;background:#f3f6fa;color:#172235}}header{{background:#122847;color:white;padding:36px max(24px,calc((100% - 1120px)/2))}}main{{max-width:1120px;margin:auto;padding:24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}}article{{background:white;border:1px solid #d9e1eb;border-radius:14px;padding:20px}}.kind{{text-transform:uppercase;font-size:12px;letter-spacing:.12em;color:#52657e}}pre{{white-space:pre-wrap;background:#f4f6f9;padding:12px;border-radius:8px}}a{{color:#1858a8}}</style></head>
<body><header><h1>VoiceShrink dashboard</h1><p>{len(entries)} saved artifact(s)</p></header><main>{''.join(cards) if cards else '<p>No artifacts found.</p>'}</main></body></html>'''
    output = root / "index.html"
    output.write_text(page, encoding="utf-8")
    return output
