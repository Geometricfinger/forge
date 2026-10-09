# Security policy

## Supported versions

Security fixes are made on the `main` branch. Please make sure you can reproduce an issue on the latest `main` before reporting it.

## Reporting a vulnerability

Please **do not** open a public issue, discussion or pull request for a security problem.

Report it privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Include:

- a description of the issue and its impact,
- the steps or a minimal synthetic input that reproduces it,
- the Forge commit you tested.

You should receive an acknowledgement through the advisory thread. Once a fix is available, the advisory will be published with credit to the reporter unless you ask otherwise.

## Scope

Forge reads untrusted source code as data and never executes it. Analysis is offline; the only outbound requests come from opt-in, operator-started GitHub discovery. [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) describes both modes. Issues of particular interest include:

- any path by which inspected code could be executed or imported,
- escapes from the workspace or output directory (path traversal, symlink handling, archive extraction),
- bypasses of the analyzer hash verification (`contracts/analyzer_lock.json`),
- network access from any offline-analysis command, or a discovery request outside the documented boundary (non-GET, a host other than `api.github.com`, a followed redirect, or budgets exceeded).

Forge's secret-pattern checks are accidental-disclosure linting, not a DLP boundary, so a pattern bypass on its own is not treated as a vulnerability.

Note that the parser resource limits are consistency controls, not a hostile-code sandbox (see the Limits section of the README). Reports that rely only on exhausting local CPU or memory with a very large input are still welcome but are treated as lower severity.
