# Architecture: which component owns what

Forge grew in layers. Hound came first, the add-on and loop were built around it, the Workbench (`forge.py` + `forge_core/`) became the main entry point, and the research runner was added on top of the Workbench. Each layer kept its own tests and contracts, so the repository still shows that history. This page tells contributors where a responsibility lives today. No refactor is implied; collapsing the layers into one canonical path is a known open item (see [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md)).

## Ownership

| Responsibility | Owner | Notes |
|---|---|---|
| CLI, workspace, local web UI | `forge.py`, `forge_core/workspace.py`, `forge_core/server.py`, `assets/` | The canonical entry point for users. |
| Release version | `forge_core/__init__.py` | See [VERSIONS.md](VERSIONS.md). |
| Python parsing and API-call detection | `hound/` | The only analyzer. Everything else calls into it; nothing else parses Python for findings. |
| Running Hound on one source with a data-only profile | `addon/tool/mission_probe.py` | Validates the Hound files against `addon/contracts/hound_binding.json` before loading them. |
| Copying and hash-verifying the analyzer; running it in an isolated process | `forge_core/engine.py` | Copies `hound/`, `addon/`, `loop/` into the workspace, checks `contracts/analyzer_lock.json`, then launches `loop/probe_worker.py`, which loads the add-on and Hound. This is why `loop/` is needed even when you never run the improvement cycle. |
| Local corpus intake (files, ZIPs, notebooks, transcripts) | `forge_core/mixed_intake.py`, `forge_core/mixed_worker.py`, `forge_core/corpus.py` | Offline. `mixed_worker.py` is the fixed parser worker for intake. |
| Search and dependency context | `forge_core/retrieval.py`, `lexical.py`, `structure.py` | BM25 + RRF over SQLite FTS5. |
| GitHub discovery (opt-in network) | `forge_core/discovery.py`, `policy.py`, `github.py`, `store.py` | The only network code. See [THREAT_MODEL.md](THREAT_MODEL.md). |
| Pinned public-source envelopes | `forge_core/github_corpus.py` | No HTTP; checks envelopes produced elsewhere. |
| Reuse review, contract trial, interop | `forge_core/reuse*.py`, `contract_trial.py`, `interop.py` | |
| Opportunity Lab | `forge_core/opportunit*.py`, `invention_plan.py` | Rule-based hypotheses from reviewed inputs. |
| Profile improvement cycle | `loop/` | Proposes and evaluates data-only profiles; never edits code or tests. Run via `forge.py cycle-demo` or `loop/run_demo.py`. |
| Multi-source research missions | `runner/` | Uses the Workbench's corpus pipeline (`forge_core.corpus`) rather than calling Hound directly. |
| Qualification | `verify.py`, `run_tests.py`, `hound/run_tests.py`, each component's tests | `verify.py` runs all of them. |
| Release hygiene | `tools/` | Manifest regeneration and the pre-publish check. |

## Rules of thumb for contributors

- New analysis behaviour goes in `hound/` (then run `python3 tools/update_analyzer_manifests.py` and review the lock diff).
- New user-facing features go in `forge_core/` and are exposed through `forge.py` and, if needed, the UI.
- `hound/reference/` and `addon/reference/` are frozen earlier versions used only by regression comparisons. Do not edit them.
- Do not add a second parser or a second network path.
