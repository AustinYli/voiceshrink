# Contributing to VoiceShrink

Thanks for helping make voice-system failures easier to reproduce.

## Good first contributions

- Add a deterministic mutation with focused tests.
- Add an adapter example for a voice framework or service.
- Improve an error message, scenario example, or report explanation.
- Reproduce and reduce a reported bug.

## Development setup

```bash
python -m venv .venv
python -m pip install -e ".[yaml]"
python -m unittest discover -s tests -v
voiceshrink doctor --json
```

Use Python 3.10 or newer. Keep core dependencies small and optional integrations isolated from the default install.

## Pull requests

1. Open an issue first for a large change so the design can be discussed.
2. Keep changes focused and include tests for behavior that could regress.
3. Update the README or changelog when users need to know about the change.
4. Run the full test suite before submitting.

New mutation families must be deterministic for a fixed scenario and seed. New target integrations must avoid logging credentials or embedding secrets in saved scenarios.

## Reporting bugs

Include the VoiceShrink version, Python version, operating system, scenario with secrets removed, command used, and the smallest safe reproduction you can share. Run `voiceshrink doctor --scenario your-scenario.yaml --json` for diagnostics.

Do not publish confidential recordings, credentials, customer data, or unsafe target endpoints in an issue.
