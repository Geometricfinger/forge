# Threat model and network boundary

This is the single reference for what Forge does and does not send over the network, and for what its secret-pattern checks are worth. Other documents link here instead of restating it.

Forge has two modes with different threat models.

## 1. Offline analysis (default)

Every analysis command reads only local files that you name and writes only to the workspace (`--home`) or a new output folder. These commands make **no network requests and no model calls**, and they never import or run the code they inspect:

- `init`, `corpus-run`, `corpus-search`, `corpus-context`, `corpus-list`, `corpus-demo`
- `opportunity-*`, `review*`, `reuse-demo`, `verify-packet`
- `contract-show`, `contract-trial`, `contract-verify`, `interop-fingerprint`, `interop-verify`
- `demo` and `cycle-demo` (the discovery demo uses a synthetic in-process transport, not the network)
- `new`, `status`, `packet`, `accept`, `export`, `cancel` (they create or move discovery work locally; `accept` ingests a response someone else collected)
- everything under `hound/`, `addon/`, `loop/` and `runner/`, plus `run_tests.py`, `verify.py` and `examples/demo/run_demo.py`

`forge.py serve` listens on `127.0.0.1` only, requires a per-session bearer token (shown once in the local URL fragment), checks `Host` and `Origin`, and serves its pages with a `'self'`-only Content Security Policy. Opening the UI does not contact any outside service.

In this mode the main risks are local: a malicious input trying to escape the workspace (path traversal, symlinks, archive bombs) or to get itself executed. Those are in scope for security reports. Parser resource limits are consistency controls, not a hostile-code sandbox.

## 2. Operator-authorized public discovery (opt-in)

Forge can look for public example code on GitHub. This is the **only** path that makes outbound requests, and it runs only when an operator explicitly starts it:

- the `forge.py run MISSION` command, or
- the **Run / resume** button on a mission in the local web UI.

It is never triggered by scanned code, by document text, or by any of the offline commands above.

**What is sent.** HTTPS `GET` requests to `https://api.github.com` only. Forge rebuilds every URL from validated fields and never follows a URL or redirect supplied by a response or repository. The requests are:

| Step | Endpoint | Data you disclose |
|---|---|---|
| Search | `/search/repositories?q=...` | The mission's search text, with `is:public fork:false archived:false` appended |
| Repository | `/repos/OWNER/NAME` | Which repository was selected |
| Commit | `/repos/OWNER/NAME/commits/BRANCH` | Its default branch |
| Tree | `/repos/OWNER/NAME/git/trees/SHA?recursive=1` | The pinned tree |
| File | `/repos/OWNER/NAME/contents/PATH?ref=SHA` | Which `.py` files were sampled |

Headers are `User-Agent: FORGE-Workbench/<release version>`, `Accept`, `X-GitHub-Api-Version` and, only if you set the `FORGE_GITHUB_TOKEN` environment variable, `Authorization: Bearer <token>`. The token is read from the environment for that process and is not written to the workspace. Nothing from your local code, workspace or reports is uploaded. GitHub does see your IP address, the token's identity (if any) and the search text.

**Limits.** Each mission carries validated budgets (hard maximums in `forge_core/policy.py`): at most 100 requests, 10 repositories, 30 files, 10 files per repository, 250 kB per source file, 4 MB of source in total, 2 MB per response and 2 attempts per request. A single `run` also stops after 100 steps or 120 seconds. Source files are fetched only from repositories that report themselves public and declare a license in the mission's allowed list (at most MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, CC0-1.0, Unlicense); other repositories are recorded as metadata only. Only `.py` files outside test, vendor and dependency folders are sampled. Rate-limit headers are honoured and the mission waits rather than retrying early. Downloaded files are analysed statically by Hound and never executed.

**Alternative without native network access.** `packet` exports one bounded GET request and `accept` ingests a response collected by another tool (for example, an agent's own GitHub connector). See [CONNECTOR_BRIDGE.md](CONNECTOR_BRIDGE.md). Forge itself makes no request in that flow.

Out of scope for this mode: GitHub's own logging, the confidentiality of whatever you type as search text, and an attacker controlling the local OS user (who can alter workspace state or forge connector responses).

## Secret-pattern checks are accidental-disclosure linting

Forge has three pattern-based checks that look for credentials or sensitive files:

| Check | Where | What it does |
|---|---|---|
| Discovery search text | `forge_core/policy.py` `query()` | Rejects search text containing URLs, `password`, `api_key`, `token=`, GitHub token prefixes, private-key headers or `is:private` before it is sent to GitHub. |
| Sensitive file names | `forge_core/mixed_intake.py` `SECRET_NAMES` | Skips files such as `.env`, `id_rsa`, `credentials.json` and `*.pem` during corpus intake. |
| Pre-publish gate | `tools/prepublish_check.py` | Scans the tree (and optionally git history) for common token formats, home-directory paths and a private denylist before you publish. |

All three are **accidental-disclosure linting**: they catch common mistakes such as a pasted token or an obvious key file. They are **not** a credential-separation mechanism, a data-loss-prevention (DLP) boundary or proof that something contains no secrets. They are easy to bypass on purpose and will miss secrets in unexpected formats or file names. Keep credentials out of the code you scan, out of discovery search text and out of anything you publish. A bypass of these patterns alone is not treated as a security vulnerability.

## Reporting

See [SECURITY.md](../SECURITY.md). Reports of particular interest: execution or import of inspected code, workspace or output escapes, analyzer-lock bypasses, any network request from an offline-mode command, and any discovery request outside the boundary described above.
