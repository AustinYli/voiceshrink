# VoiceShrink implementation status

The project-plan milestones are implemented for the local, single-process product scope.

| Milestone | Status | Implementation |
|---|---|---|
| M0 Core schemas | Complete | Scenario, results, outcomes, fingerprints, mutations, candidates, and evaluations |
| M1 Target adapters | Complete | Python, command, and HTTP file targets plus streaming and intervention variants |
| M2 Failure oracle | Complete | Structured, transcript, exact, same-category, and custom equivalence |
| M3 Reference targets | Complete | Pause, region, combination, non-monotonic, stochastic, streaming, and investigation fixtures |
| M4 Audio mutations | Complete | Silence, noise, gain, dropout, clipping, speed, bandwidth, resampling, μ-law, quantization, and crop |
| M5 Discovery | Complete | Escalating singles, temporal variation, bounded pairs, and multiple distinct failures |
| M6 Mutation-set shrinking | Complete | Whole-operator removal with same-failure preservation |
| M7 Temporal/parameter shrinking | Complete | Region and strength proposals without a monotonicity assumption |
| M8 Nondeterminism | Complete | Repeated trials, thresholds, baseline trials, concurrency, and inconclusive outcomes |
| M9 Regression artifacts | Complete | Portable bundles, audio, mutation programs, observations, metadata, and replay |
| M10 Reports | Complete | Terminal, JSON, HTML, collection dashboards, waveforms, privacy controls, and integrity verification |
| M11 Stage tracing | Complete | Earliest observed divergence reporting |
| M12 Intervention | Complete | Python, command, and HTTP interventions with restoration evidence |
| M13 Streaming faults | Complete | Packet loss, jitter, chunk delay, reorder, shrinking, artifacts, and replay |
| M14 Investigate mode | Complete | Bounded contiguous-window reduction for an already-failing clip |
| Real ASR reference | Complete | faster-whisper `tiny.en` baseline, discovery, minimization, and offline regression replay |

## Verification

- Deterministic unit, algorithm, stochastic, adapter, transport, privacy, and artifact tests
- Acoustic, streaming, and investigation end-to-end demos
- Real faster-whisper discovery and offline replay
- Python 3.10–3.13 CI matrix
- Installable wheel smoke test
- JUnit and JSON CI output, environment-backed secrets, and runtime diagnostics
- Regression reduction metrics and expanded reproducibility fingerprints
- Audio-contract invariant coverage for every acoustic mutation family
- Side-effect-free preflight validation before target execution
- Tested non-monotonic shrinking across a passing parameter region
- Normalized corrupt-audio and target-timeout diagnostics
- Boundary-aware temporal candidate generation for speech transitions and quiet gaps
- Bounded seeded higher-order mutation interaction search
- Regression discovery now reports missing and malformed bundles instead of skipping them
- Pair budgets count only emitted cross-family candidates

## Product boundaries

The project remains local and single-process. Distributed execution, hosted user accounts, telephony, semantic rewriting, speaker cloning, TTS evaluation, and automatic bug fixing remain outside the plan’s stated scope.
