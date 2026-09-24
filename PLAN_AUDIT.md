# VoiceShrink project-plan audit

This audit compares the implementation with the 43 sections in the supplied project plan. The attached HTML is treated as a design reference; the implementation uses consolidated Python modules where that keeps a small local package easier to maintain.

| Plan section | Status | Evidence |
|---|---|---|
| 1–3 Product, architecture, modes | Complete | Known-good mutation programs, Discover mode, and bounded Investigate mode |
| 4–6 Target interface and observability | Complete | Base `Target`, Python, command, HTTP, streaming, stage traces, and interventions |
| 7 Scenario definition | Complete | JSON and YAML scenarios with expected behavior, target, oracle, search, evaluation, privacy, streaming, and investigation settings |
| 8 Failure oracle | Complete | PASS, FAIL_SAME, FAIL_OTHER, INVALID, TARGET_ERROR, INCONCLUSIVE, fingerprints, and three equivalence modes |
| 9 Baseline validation | Complete | Repeated clean trials must reliably pass before discovery |
| 10–11 Mutations and programs | Complete | Serializable acoustic and transport operators with saved programs |
| 12 Discovery engine | Complete | Escalating singles, fixed and detected speech-boundary temporal variants, bounded pairs, opt-in seeded higher-order combinations, and multiple distinct failures |
| 13 Budgets and caching | Complete | Hard execution limits, concurrency, fingerprinted SQLite caching, cache bypass, and cache privacy control |
| 14–17 Shrinking and complexity | Complete | Operator removal, temporal reduction, strength reduction, and lexicographic complexity without a monotonicity assumption |
| 18 Nondeterminism | Complete | Repeated trials, thresholds, baseline trials, and concurrent evaluation |
| 19 Reproducibility fingerprint | Complete | Version, OS, Python, audio/scenario/target hashes, mutation seeds, target fingerprint, optional model/prompt/schema declarations, and target metadata |
| 20 Optional LLM usage | Complete | Core has no LLM dependency; an LLM may exist inside a user target |
| 21–22 Attribution | Complete | Earliest observed divergence plus optional intervention evidence |
| 23 Safety | Complete | Documentation recommends staging, dry-run targets, and mocked tools |
| 24 Privacy | Complete | Local-first processing, output redaction, hidden audio controls, and optional cache disabling |
| 25–26 Regression artifacts and runner | Complete | Portable bundles, integrity manifests, replay, JSON, JUnit, and resolved/changed/reproducing states |
| 27 CLI | Complete | Init, baseline, discover, investigate, validate, test, report, verify, dashboard, and doctor commands |
| 28 Report UI | Complete | Side-by-side audio, waveforms, mutation region, outputs, fingerprints, reproduction, reduction, and attribution |
| 29 Reference targets | Complete | Pause, region, combination, non-monotonic, stochastic, streaming, and production-failure fixtures |
| 30 Real integration | Complete | Optional faster-whisper adapter and completed `tiny.en` example |
| 31–32 Tests and invariants | Complete | Unit, algorithm, stochastic, adapter, transport, integration, privacy, artifact, and every-acoustic-operator contract tests |
| 33 Product metrics | Complete | Executions, cache hits, runtime, cost, mutation reduction, affected-duration reduction, complexity, failure-rate increase, and reproduction |
| 34 Repository structure | Complete | Equivalent responsibilities are kept in focused modules under `src/voiceshrink` |
| 35–37 Data model and algorithms | Complete | Dataclasses for core values plus budgeted discovery and iterative same-failure shrinking |
| 38 Milestones | Complete | M0 through M14 are implemented |
| 39 Non-goals | Complete | The package remains centered on local discovery, reduction, reproduction, and regression testing |
| 40 First end-to-end demo | Complete | Deterministic pause and regional demos discover, shrink, save, report, and replay |
| 41 First real-system demo | Complete | Real speech and faster-whisper produced a minimized deterministic transcript failure |
| 42–43 Product story and definition | Complete | Installable package and stack-agnostic scenario workflow match the described product |

## Verification baseline

- Python 3.10–3.13 CI configuration
- Local automated test suite
- Installable wheel smoke test
- Source archive rebuild check
- Acoustic, streaming, investigation, and real-ASR artifacts with integrity manifests
