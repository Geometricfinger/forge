# Contributing to Forge

Thank you for your interest in improving Forge. This guide explains how to propose changes.

## Ground rules

- Forge has **no third-party runtime dependencies**. Please do not add any. Standard library only, Python 3.11+.
- Forge must stay **read-only**, never execute inspected code and call no models.
- Keep the network boundary in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): analysis is **offline** (no network requests), and the only network path is **operator-authorized public discovery** (GET-only, bounded requests to `api.github.com`, started explicitly by the operator). Do not add network access to analysis commands or a second network path. Changes that weaken either side need a strong justification and an open discussion first.
- Secret-pattern checks are accidental-disclosure linting. Do not describe them, in code or docs, as a credential-separation or DLP boundary.
- See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for which component owns what, and [docs/VERSIONS.md](docs/VERSIONS.md) for how versions work (the release version lives only in `forge_core/__init__.py`).
- Keep pull requests small and focused on one change.
- All contributions are made under the Apache License 2.0 (see `LICENSE`). Only submit code you wrote or have the right to contribute under that license. Do not paste code from sources with incompatible licenses.
- Never commit secrets, personal data, absolute home-directory paths or real scan output. Use synthetic examples, like the ones in `examples/demo/`.

## Getting started

1. Fork the repository and create a branch from `main`.
2. Make your change and add or update tests next to the code you touched (`tests/`, `hound/tests/`, `loop/tests/`, `runner/tests/`).
3. Run the checks locally. Output folders must be new and outside the repository:

   ```sh
   python3 run_tests.py --out /tmp/forge-tests
   python3 hound/run_tests.py --out /tmp/hound-tests
   (cd runner && python3 -m unittest discover -s tests)
   python3 examples/demo/run_demo.py --out /tmp/forge-demo
   python3 verify.py --out /tmp/forge-qualification
   python3 tools/prepublish_check.py --history
   ```

4. If you intentionally changed files under `hound/`, `addon/` or `loop/`, regenerate the analyzer manifests and include the diff in your pull request:

   ```sh
   python3 tools/update_analyzer_manifests.py
   ```

5. Open a pull request using the template. CI (`.github/workflows/qualification.yml`) re-runs the pre-publish check, `verify.py`, the runner tests and both demos on Python 3.11 and 3.13 against your pull request's head commit, and uploads the evidence as the artifact `forge-verification-<commit-sha>-py<version>`. Every commit on `main` is verified the same way. The `qualify (Python 3.11)` and `qualify (Python 3.13)` checks must pass before a change can be merged.

## Reporting bugs and requesting features

Use the issue templates. For bugs, include the Forge command you ran, your Python version and operating system, and the smallest input that reproduces the problem. Do not attach private source code; reduce it to a synthetic example.

Security problems should **not** be reported in public issues. See [SECURITY.md](SECURITY.md).

## Code style

- Follow the style of the surrounding code.
- Prefer clear, deterministic behaviour over cleverness. The same input must produce the same output.
- Write errors that tell the user what to do next.
