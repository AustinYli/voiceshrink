# VoiceShrink developer-readiness report

## Technical status

VoiceShrink is technically usable as a local Python package for trusted developer workflows. The release gate covers installation, configuration validation, baseline checks, acoustic discovery, streaming discovery, investigation reduction, regression export, integrity verification, replay, dashboards, source builds, and the optional local faster-whisper integration.

## Latest release audit

- 39 automated tests passed.
- The 0.2.6 wheel installed into an empty temporary package directory.
- Fresh acoustic discovery, streaming discovery, and investigation workflows completed from the installed wheel.
- Saved acoustic and streaming failures replayed with the expected nonzero regression exit status.
- A real localhost HTTP target received WAV bytes and returned a valid structured result.
- Both saved faster-whisper JFK failures reproduced offline at 100%.
- A wheel rebuilt from the source archive matched the direct wheel member-for-member.
- Release SHA-256 checksums passed.

The audit fixed pair-budget accounting, missing-bundle discovery, malformed integrity-manifest handling, and unsafe integrity paths.

The packaged Windows end-to-end gate completed all three operating workflows from a clean temporary install. Python, command, and real localhost HTTP target execution are covered. The repository includes a Python 3.10–3.13 Linux CI matrix; those hosted jobs require the project to be pushed to a GitHub repository before they can provide independent Linux evidence.

## Supported environment

- Python 3.10 or newer
- Windows, macOS, or Linux compatible Python runtime
- Uncompressed 16-bit PCM mono or stereo WAV input
- Local Python, command, HTTP, or optional faster-whisper targets
- JSON scenarios and YAML scenarios through the optional PyYAML extra or bundled basic subset parser

## Operational requirements

- Run repeated tests against staging, dry-run, or mocked targets.
- Treat regression bundles as trusted code-adjacent inputs because replay executes their configured targets.
- Set `target.fingerprint` whenever the tested model, prompt, tool schema, or application version changes.
- Use environment-backed command variables or HTTP headers for secrets.
- Keep `search.max_concurrency: 1` for Python targets that are not thread safe.

## Known boundaries

- Audio input is limited to uncompressed 16-bit PCM WAV.
- The codec mutation is a local μ-law simulation.
- HTML reports are static local files.
- Distributed execution, hosted accounts, telephony, automatic repairs, semantic rewriting, TTS evaluation, and speaker cloning remain outside the project scope.
- Integrity manifests detect local changes but are not signed authenticity proofs.

## Distribution status

The repository includes an MIT license, contributor guidance, a security policy, issue forms, pull request guidance, a Python 3.10–3.13 CI matrix, and validated wheel and source archives for the v0.2.6 GitHub release.
