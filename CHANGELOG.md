# Changelog

## 0.2.6

- Fixed pair limits so only emitted cross-family pairs consume the configured budget.
- Regression suites now report bundles with missing scenario files instead of silently skipping them.
- Integrity verification now handles malformed manifests and rejects unsafe paths and unsupported manifest shapes.
- Added a real localhost HTTP adapter round-trip test in addition to protocol-level mocks.

## 0.2.5

- Added optional bounded higher-order mutation searches with configurable size and count.
- Added deterministic seeded ordering for higher-order candidate programs.
- Added validation and a planted three-family interaction target proving discovery and same-failure shrinking.

## 0.2.4

- Added deterministic energy-based detection of speech/quiet transitions and quiet-region midpoints.
- Added discovered speech boundaries to silence, dropout, and region-based acoustic candidate generation.
- Capped boundary candidates deterministically to preserve search-budget predictability.

## 0.2.3

- Added a non-monotonic shrink test that crosses a passing region to find a smaller reproducing mutation.
- Added an additional non-monotonic duration probe during silence and dropout shrinking.
- Normalized corrupt WAV and invalid sample-rate failures to clear validation errors.
- Added explicit timeout errors for command, streaming-command, and intervention-command targets.

## 0.2.2

- Added shared, side-effect-free scenario validation before engine or target execution.
- Added checks for target requirements, URLs, budgets, concurrency, costs, mutation families, evaluation thresholds, streaming/investigation settings, oracle modes, and privacy value types.
- Added `voiceshrink --version` for runtime diagnostics.

## 0.2.1

- Added mutation-count, affected-duration, complexity, and failure-rate metrics to regression metadata.
- Added target-configuration hashes, mutation seeds, optional prompt/schema hashes, model declarations, and target-result metadata to the reproducibility fingerprint.
- Added reduction summaries to terminal and HTML discovery reports.
- Added CLI help descriptions for baseline and discovery commands.
- Regression suites now reject bundles whose integrity manifest is missing or invalid before loading their scenarios.
- Dashboards now include artifact version, reduction, runtime, execution, cost, budget, and integrity details.

## 0.2.0

- Added deterministic streaming plans and packet loss, jitter, chunk delay, and reorder mutations.
- Added streaming adapters for Python, command, and HTTP targets.
- Added shrinking, caching, reports, artifacts, and regression replay for transport failures.
- Added bounded Investigate mode for reducing an already-failing clip to a contiguous reproducing window.
- Added intervention-capable targets and evidence-based stage attribution.
- Added optional local faster-whisper integration.
- Added configurable concurrency for repeated target trials.
- Added local acoustic, streaming, and investigation demos.
- Added bounded multi-failure discovery with one minimized bundle per distinct fingerprint.
- Added scenario validation and machine-readable discovery/investigation output.
- Added HTML report output redaction and audio hiding options.
- Completed a real faster-whisper baseline, discovery, minimization, report, and offline regression replay.
- Fixed portable model-cache paths in saved ASR regression scenarios.
- Added SHA-256 artifact manifests and the `verify` command.
- Added local HTML/JSON dashboards for regression and investigation collections.
- Moved replay caches outside artifact bundles and cleaned nested legacy caches.
- Added JSON and JUnit XML regression-suite output for CI.
- Added runtime and optional per-execution cost metrics.
- Added environment-backed command variables and HTTP headers for secret-free scenarios.
- Added the `doctor` runtime and scenario diagnostics command.
- Added a privacy option to disable persistent intermediate-result caching.

## 0.1.0

- Initial Discover workflow with acoustic mutations, same-failure shrinking, adapters, caching, regression replay, and HTML reports.
