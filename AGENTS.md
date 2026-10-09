# AGENTS.md: instructions for AI coding agents

This file tells AI coding agents (Codex, Cursor, Copilot and similar) how to install Forge, point it at a user's code, and read the results. Humans should start with [README.md](README.md).

## What Forge is

Forge is a local, read-only static-analysis workbench for Python source. You give it an explicit list of files (a manifest) and a list of exact APIs to look for (a profile). It parses the files, records definitions, imports and calls, reports every call to a profiled API that it can bind to a declared import, and builds a local search index over that metadata.

What Forge does **not** do:

- It never imports, runs or installs the code it scans.
- The code-analysis commands in this file make no network requests and no model calls. (The `new`/`run`/`packet`/`accept` GitHub discovery commands can make GET requests to `api.github.com`; you do not need them to analyse local code.)
- It only analyses Python: `.py`, `.pyi`, `.pyw`, Jupyter notebooks, and `python` code fences inside `.md`/`.txt`/`.rst` files. Other languages are recorded as `LANGUAGE_NOT_ANALYZED`.
- Its parser limits are consistency controls, not a sandbox for hostile code.
- Its output is a set of leads for human review. It is not proof of a vulnerability, a quality score, or permission to reuse code. A missing match does not prove a capability is absent.

## Requirements

- Python 3.11 or newer with SQLite FTS5 (included in standard CPython builds). Check with `python3 --version`.
- Linux or macOS. Windows is not supported.
- No third-party packages, no `pip install`, no build step. Node.js is optional and only used by `verify.py`.

```sh
git clone <this repository URL> forge
cd forge
python3 forge.py --help
```

## Rules for agents

1. **Do not modify Forge** while analysing someone's code. The workspace is bound to the exact Forge source; any change to it invalidates existing workspaces (`WORKBENCH_CODE_CHANGED_NEW_HOME_REQUIRED`).
2. **Do not modify the target code.** Forge only reads it. Do not write manifests, profiles or workspaces into the target folder.
3. **Keep the workspace outside both the Forge checkout and the target folder.** Forge refuses a workspace inside its own checkout and refuses run output inside the target, but it may already have created the workspace folder by then. Use the separate `WORK` folder shown below.
4. **Do not upload, paste or send the user's code or Forge's output anywhere** (issue trackers, pastebins, chat services, other APIs) unless the user explicitly asks. Everything stays on the local machine.
5. **Do not run the scanned code** to "confirm" a finding unless the user explicitly asks and understands the risk.
6. **Report findings as leads, not proven vulnerabilities.** Always give file, line and function, say that the result is static and unverified at runtime, and mention any gaps or unanalysed files.
7. **Do not start `forge.py serve` unattended.** It runs until stopped and prints a local URL containing an access token; treat that URL as a secret.

## Analyse a user's codebase (verified recipe)

Run these blocks in order in one shell session. They work from any current directory.

### 1. Set paths

```sh
FORGE=/absolute/path/to/forge          # this repository
TARGET=/absolute/path/to/their/code    # folder to analyse (read only)
WORK="$HOME/forge-work"                # workspace: outside FORGE and TARGET
RUN=myproject                          # run id: letters, digits, _ and - (max 80)

mkdir -p "$WORK"
# Forge rejects paths that pass through a symlink (for example /tmp on macOS),
# so resolve every path to its physical location first.
FORGE="$(cd "$FORGE" && pwd -P)"; TARGET="$(cd "$TARGET" && pwd -P)"; WORK="$(cd "$WORK" && pwd -P)"
```

If `TARGET` is your home folder or contains `$HOME/forge-work`, choose a `WORK` elsewhere.

### 2. Build a manifest

`corpus-run` reads only the files listed in a manifest. Each entry pins the file's relative path, size and SHA-256. For local files use `"provider": "synthetic"` with `"source_url": "fixture://<file_id>"`; this is the local-file provider and does not mean the code is fake. This script lists every Python file and notebook under `TARGET`, skipping VCS, virtualenv, dependency and build folders:

```sh
python3 - "$TARGET" "$WORK/$RUN-manifest.json" <<'PY'
import hashlib, json, os, sys
root, out = os.path.realpath(sys.argv[1]), sys.argv[2]
skip = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', 'node_modules', 'site-packages',
        '__pycache__', 'vendor', 'build', 'dist', '.tox', '.nox', '.eggs'}
files = []
for d, dirs, names in os.walk(root):
    dirs[:] = sorted(x for x in dirs if x not in skip and not os.path.islink(os.path.join(d, x)))
    for n in sorted(names):
        p = os.path.join(d, n)
        rel = os.path.relpath(p, root).replace(os.sep, '/')
        if not n.endswith(('.py', '.pyi', '.pyw', '.ipynb')) or os.path.islink(p) or ':' in rel or len(rel) > 200:
            continue
        data = open(p, 'rb').read()
        if len(data) > 8_000_000:
            continue
        files.append({'provider': 'synthetic', 'file_id': rel, 'path': rel, 'size': len(data),
                      'sha256': hashlib.sha256(data).hexdigest(), 'source_url': 'fixture://' + rel})
if not files:
    sys.exit('No Python files found under ' + root)
if len(files) > 500:
    sys.exit(f'{len(files)} files found; a run takes at most 500. Point TARGET at a subfolder.')
with open(out, 'w') as f:
    json.dump({'schema': 1, 'files': files}, f, indent=1)
print(len(files), 'files ->', out)
PY
```

Manifest rules (enforced by `forge_core/corpus.py`): `schema` is `1`; 1 to 500 files; every `file_id` and `path` is unique; `path` is relative POSIX with no `..`, `\` or `:`; each file is at most 8 MB. ZIP archives can also be listed as a single entry and are read member by member.

### 3. Choose a profile

A profile is a data-only JSON list of the exact, fully qualified APIs to report (1 to 128 targets). For a security review, start from the bundled risky-call profile (pickle, subprocess, `os.system`, `eval`/`exec`, `yaml.load`, tar/zip extraction, sqlite, XML parsing):

```sh
cp "$FORGE/examples/demo/profile.json" "$WORK/profile.json"
```

To look for other APIs, edit `$WORK/profile.json`. The format is:

```json
{"schema": 1, "id": "my-profile", "title": "What this profile looks for",
 "targets": [{"api": "requests.get", "capability": "http_get"},
             {"api": "builtins.eval", "capability": "eval"}]}
```

`id` uses lowercase letters, digits, `_` and `-`; `api` is a dotted name such as `module.function` or `module.Class.method` (use `builtins.` for built-ins); `capability` is your own lowercase label. Without `--profile`, Forge uses `templates/corpus_profile.json`.

### 4. Run the scan

```sh
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-run --source-root "$TARGET" \
    --manifest "$WORK/$RUN-manifest.json" --run-id "$RUN" --profile "$WORK/profile.json" \
    --max-containers 500
```

The workspace (`$WORK/forge-home`) is created on first use. Always pass `--max-containers 500`; the default of 20 files per invocation leaves the rest as `INVOCATION_BUDGET` (status `INCOMPLETE`). Re-running the same command resumes from checkpoints.

The command prints one JSON object. Check `status`:

| `status` | Exit code | Meaning |
|---|---|---|
| `COMPLETED_FOR_SELECTED_CONTAINERS` | 0 | Every listed file was analysed. |
| `COMPLETED_WITH_GAPS` | 2 | Results are usable, but some files or blocks could not be parsed (for example Python 2 syntax). Report the gaps. |
| `INCOMPLETE` | 2 | Some files were not read (`coverage.unread`) or were rejected (`coverage.blocked`). See Troubleshooting. |
| `BLOCKED` | 2 | Nothing was produced; `reason` names the problem. See Troubleshooting. |

Results are written to `$WORK/forge-home/corpora/$RUN/`:

- `corpus.json`: the full report (sources, segments, definitions, Hound findings, gaps, coverage). Source bodies are not stored.
- `Review.html`: a self-contained, searchable page for a person to open in a browser.
- `capabilities.ndjson.gz`: one search record per definition or module scope.
- `receipt.json`: SHA-256 hashes binding the report.

### 5. List the findings

This prints one line per profiled API call, then any gaps and unanalysed files:

```sh
python3 - "$WORK/forge-home/corpora/$RUN/corpus.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
cov = report['coverage']
print('status:', report['status'], '| files analysed:', cov['inspected_containers'], 'of', cov['selected_containers'])
for src in report['sources']:
    for seg in src['segments']:
        for f in seg.get('hound', {}).get('findings', []):
            line = seg['start_line'] + f['evidence'][0]['line_start'] - 1
            cell = '' if seg['cell_index'] is None else f" (notebook cell {seg['cell_index']})"
            where = f.get('qualified_name') or f.get('scope_name', '<module>')
            test = '  [test code]' if seg['test_path'] else ''
            print(f"{src['source']['path']}:{line}{cell}  {f['resolved_api']}  in {where}  [{f['capability']}]{test}")
for gap in report['gaps']:
    print('GAP', gap.get('file_id'), gap.get('status') or gap.get('reason'), 'line', gap.get('syntax_line', '-'))
for row in cov['blocked'] + cov['unread']:
    print('NOT ANALYSED', row['file_id'], row['reason'])
PY
```

Line numbers are file lines for `.py` files and cell-local lines for notebook cells. Open the referenced source yourself and judge each call in context (where the argument comes from, whether it is reachable, whether it is test code) before you report it.

### 6. Search and drill down

```sh
# Ranked search over names, docstrings, paths, imports and observed APIs (test code excluded by default)
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-search --run-id "$RUN" --query "subprocess run"

# Only results that contain an observed call to a given API
echo '{"required_apis": ["subprocess.run"]}' > "$WORK/filters.json"
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-search --run-id "$RUN" --query "run" --filters "$WORK/filters.json"

# Callers/callees context for one result (use a definition_id from the search output)
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-context --run-id "$RUN" --definition definition_<64 hex chars>

# Export a review packet (up to 50 results with dependency context) to a new folder
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-search --run-id "$RUN" --query "subprocess run" --out "$WORK/$RUN-packet"

# List all runs in the workspace
python3 "$FORGE/forge.py" --home "$WORK/forge-home" corpus-list
```

Search options: `--include-tests` adds test code, `--distinct` groups byte-identical duplicates, and `--filters` accepts `required_apis`, `exclude_apis`, `record_kind` (`function` or `source_scope`) and `container_ids` (manifest `file_id`s). Each result has `path`, `name`, `start_line`, `end_line`, `observed_apis` and `definition_id`. Ranking is lexical relevance, not confidence. `search_status: NO_METADATA_MATCH` does not mean the capability is absent.

### 7. Rescan after the code changes

The manifest pins file hashes, and a run id is bound to its manifest and profile. After the code or profile changes, rebuild the manifest (step 2) and use a **new** `RUN` id.

## Other commands

- Bundled demo, end to end: `python3 "$FORGE/examples/demo/run_demo.py" --out "$WORK/demo"` (the folder must not exist). It should print `"scan_status": "COMPLETED_FOR_SELECTED_CONTAINERS"` and exit 0.
- Local web UI: `python3 "$FORGE/forge.py" --home "$WORK/forge-home" serve` (binds to `127.0.0.1` only; add `--no-browser` to just print the URL). Only start it if the user asks, and stop it afterwards.
- Opportunity Lab (`opportunity-demo`, `opportunity-run`, etc.), reuse review (`review-*`, `reuse-demo`) and contract trial (`contract-*`): see `python3 forge.py <command> --help` and the code in `forge_core/`. You do not need them for code analysis.
- Research runner (`runner/`): bounded multi-source research missions that produce a review packet. Run its demo with `python3 "$FORGE/runner/run_demo.py" --out "$WORK/research-demo"`. Custom missions need a full mission file in the format of `runner/contracts/MISSION.json`; see `runner/README.md`.

## Verify the Forge installation

Output folders must be new and outside the repository. Run from the Forge checkout:

```sh
cd "$FORGE"
python3 run_tests.py --out "$WORK/tests"                 # Workbench suite
python3 hound/run_tests.py --out "$WORK/hound-tests"     # Hound suite
(cd runner && python3 -m unittest discover -s tests)     # Research runner, must run from runner/
python3 verify.py --out "$WORK/qualification"            # Full qualification (a minute or more)
```

## Troubleshooting

| Error or symptom | Cause and fix |
|---|---|
| `SYMLINK_PATH` | A path passes through a symlink (on macOS `/tmp` is one). Use `pwd -P` as in step 1, or a folder under `$HOME`. |
| `SEPARATE_WORKSPACE_REQUIRED` | `--home` is inside the Forge checkout. Use `$WORK/forge-home`. |
| `CORPUS_SEPARATE_OUTPUT` | The workspace is inside `TARGET` (or the reverse). Move `WORK` elsewhere and delete any workspace folder Forge created inside the target. |
| `SOURCE_ROOT` | `--source-root` is not an existing folder. |
| `CORPUS_MANIFEST`, `CORPUS_SOURCE_FIELDS`, `CORPUS_SOURCE_URL`, `CORPUS_PROVIDER`, `DUPLICATE_CORPUS_SOURCE`, `REPOSITORY_PATH`, `CORPUS_COUNT` | The manifest is malformed. Regenerate it with step 2. |
| `SOURCE_VERSION_MISMATCH` in `coverage.blocked` | The file changed after the manifest was built. Rebuild the manifest and use a new run id. |
| `CORPUS_BINDING_CHANGED_NEW_RUN_REQUIRED` | The run id was used with a different manifest, profile or source root. Use a new run id. |
| `INVOCATION_BUDGET` in `coverage.unread` | You left out `--max-containers 500`. Re-run the same command with it; finished files are reused. |
| `CORPUS_RUNNING` | Another `corpus-run` for this run id is in progress. Wait for it. |
| `CORPUS_RUN_ID` | The run id has characters other than letters, digits, `_` and `-`, or is longer than 80. |
| `PROFILE_SCHEMA`, `PROFILE_ID`, `TARGET_SCHEMA`, `TARGET_COUNT`, `EXACT_API_REQUIRED`, `CAPABILITY_LABEL` | The profile is malformed. Use exactly the keys `schema`, `id`, `title`, `targets`; each target has only `api` (dotted name, no duplicates) and `capability` (lowercase label). |
| `INVALID_JSON`, `DUPLICATE_JSON_KEY` | A manifest, profile or filter file is not strict JSON. |
| `WORKBENCH_CODE_CHANGED_NEW_HOME_REQUIRED` | The Forge source changed (for example after `git pull`) since the workspace was created. Use a new `--home`. |
| `PYTHON_SYNTAX_ERROR` gap | The file does not parse with the running Python (often Python 2 code). Report it as not analysed. |
| `ModuleNotFoundError`/errors when running runner tests from the repository root | Run them from `runner/`: `(cd runner && python3 -m unittest discover -s tests)`. |
| `Choose a new output directory` / `NEW_EXTERNAL_OUTPUT_REQUIRED` | Test, demo and verify output folders must not exist yet and must be outside the repository. |

## Contributing changes to Forge

Only when the user asks you to change Forge itself (not when analysing their code):

- Read [CONTRIBUTING.md](CONTRIBUTING.md). Keep pull requests small and add or update tests next to the code you change.
- Standard library only. Do not add third-party runtime dependencies, and do not add code or dependencies under licenses that restrict commercial use or are incompatible with Apache-2.0.
- If you change `hound/`, `addon/` or `loop/`, run `python3 tools/update_analyzer_manifests.py` and include the diff.
- Before opening a pull request, `python3 verify.py --out <new folder outside the repo>` and `python3 tools/prepublish_check.py --history` must pass.
- Never commit secrets, personal data, absolute home-directory paths or real scan output. Use synthetic examples like `examples/demo/`.
- Report security problems privately as described in [SECURITY.md](SECURITY.md), not in public issues.
