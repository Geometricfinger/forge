#!/usr/bin/env python3
"""Regenerate docs/ERROR_CODES.md from the Blocked('CODE') sites in the source.

Usage: python3 tools/gen_error_codes.py [--check]
--check exits 1 if docs/ERROR_CODES.md is missing a code (used by the tests).
"""
import re,sys
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PAT=re.compile(r"Blocked\('([A-Z][A-Z0-9_]*)'")
SKIP={'third_party','dependencies','history','evidence','review','__pycache__'}

# Hand-written meanings for the codes a newcomer is most likely to hit.
KNOWN={
 'NO_AVAILABLE_REQUEST_CHECK_STATUS':'`packet` has nothing to hand out right now. Either a packet is already out (accept its response or wait ~5 min for the lease to expire), the GitHub provider is cooling down / on hold / out of request budget, or every task is DONE or BLOCKED (e.g. after `run`). The JSON now carries a `detail.explanation` saying which. Check `forge.py status --mission ID`.',
 'INVALID_JSON':'The file is not valid UTF-8 JSON. Commands that take JSON (`--bundle`, `--contract`, `--filters`, `review-note --file`, ...) need a JSON object. `opportunity-draft --file` also accepts plain-text notes; a file whose first character is `{` is parsed as JSON.',
 'REUSE_RECORD_FIELDS':'The `review-new` contract has missing or extra fields. A requirement contract needs exactly `schema` (1), `title`, `objective`, `requirements`. `contracts/reuse_trial.json` is a frozen test fixture, not a contract; use the default `templates/reuse_contract.json` (or omit `--contract`).',
 'REUSE_SCHEMA':'Contract `schema` must be the integer 1.',
 'REQUIREMENT_COUNT':'A contract needs 1 to 20 `requirements`.',
 'REQUIREMENT_KIND':'Requirement `kind` must be `observed_api`, `review` or `behavior_test`.',
 'REQUIREMENT_ID':'Requirement `id` must be unique, lowercase, start with a letter, and use only a-z 0-9 _ -.',
 'REQUIRED_CRITERION_MISSING':'At least one requirement must have `"required": true`.',
 'EXACT_API_TARGETS_REQUIRED':'An `observed_api` requirement needs `apis`: 1-10 unique dotted names such as `hashlib.sha256`.',
 'NO_SOURCE_CANDIDATES':'The mission has no collected source candidates yet. Run `forge.py run MISSION` (or `packet`/`accept`) first so there is something to review.',
 'SEPARATE_WORKSPACE_REQUIRED':'`--home` is inside the Forge package (or the package is inside it). Use a home outside the repository, e.g. `--home ~/forge-home`.',
 'OUTPUT_CONFLICT':'The output path already exists. Forge never overwrites; pick a new path.',
 'SOURCE_ROOT':'`--source-root` (or the `make-manifest` folder) is not an existing directory.',
 'SOURCE_ESCAPE':'A manifest `path` points outside `--source-root`.',
 'SOURCE_VERSION_MISMATCH':'A file in `--source-root` does not match the `size`/`sha256` recorded in the manifest. Re-create the manifest (`forge.py make-manifest DIR --out FILE`) or restore the file.',
 'CORPUS_MANIFEST':'Manifest must be `{"schema": 1, "files": [...]}`.',
 'CORPUS_COUNT':'A manifest (or a plain folder) may list 1 to 500 files.',
 'CORPUS_SOURCE_FIELDS':'Each manifest row needs `file_id`, `path`, `size`, `sha256`, `source_url` (plus optional `provider`).',
 'CORPUS_SOURCE_SHA':'Row `sha256` must be 64 lowercase hex characters.',
 'CORPUS_PROVIDER':'Row `provider` must be `local`, `gdrive`, `github` or `synthetic` (default `gdrive`).',
 'CORPUS_SOURCE_URL':'Row `source_url` does not match its provider: `local` -> `local://<path>`, `synthetic` -> `fixture://<file_id>`, `gdrive` -> `https://drive.google.com/file/d/<file_id>/view`.',
 'DUPLICATE_CORPUS_SOURCE':'Two manifest rows share a `file_id` or `path`.',
 'CORPUS_RUN_ID':'`--run-id` must be 1-80 characters of A-Z a-z 0-9 _ -.',
 'CORPUS_SEPARATE_OUTPUT':'The corpus output must be outside the Forge package and outside `--source-root`. Use a `--home` outside both.',
 'CORPUS_BINDING_CHANGED_NEW_RUN_REQUIRED':'This `--run-id` was already used with a different manifest, folder contents, profile or code. Use a new `--run-id`.',
 'LOCAL_FOLDER_EMPTY':'The plain folder has no supported files (.py .pyi .pyw .ipynb .zip .txt .md .markdown .rst; hidden files are skipped).',
 'REPOSITORY_PATH':'A path is absolute, contains `..`, `\\\\` or `:`, or is not normalised.',
 'WORKSPACE_NOT_INITIALIZED':'The `--home` folder is not a Forge home. Run `forge.py --home DIR init` first.',
 'WORKBENCH_CODE_CHANGED_NEW_HOME_REQUIRED':'The Forge code changed since this home was created. Use a new `--home`.',
 'MISSION_NOT_FOUND':'No mission with that id in this `--home`. See `forge.py status`.',
 'MISSION_CANCELLED':'The mission was cancelled; create a new one.',
 'STALE_CLAIM':'The packet token no longer matches (it expired or was re-issued). Run `packet` again.',
 'REQUEST_BINDING':'The response packet does not belong to a task of this mission.',
 'RESPONSE_ENVELOPE':'`accept --response` must be a JSON object with exactly `status`, `body`, `headers`, `provenance`.',
 'TEMPLATE_NOT_FOUND':'Unknown mission template; use understanding, reliability or evaluation.',
 'DUPLICATE_JSON_KEY':'The JSON has the same key twice in one object.',
 'NONFINITE_JSON':'The JSON contains NaN or Infinity.',
 'OPPORTUNITY_ID':'An id must start with a letter or digit and use only A-Z a-z 0-9 _ - (max 80).',
 'SOURCE_TEXT_BOUNDS':'Notes/source text is empty, contains NUL bytes, or is over 24,000 characters.',
 'SOURCE_URI':'Source `uri` must be https, fixture, conversation or file-ref, with a host and no credentials.',
 'PUBLIC_SOURCE_URI':'A source marked `public: true` must have an https `uri`.',
 'FUTURE_SOURCE':'`observed_on` is in the future.',
 'OPPORTUNITY_SOURCE_KIND':'Source `kind` must be one of firsthand, product_listing, product_docs, issue, research, source_review, synthetic, and `public` a boolean.',
 'SOURCE_CAPTURE_METHOD':'`capture_method` must be attributed_summary, verbatim_excerpt or synthetic_fixture.',
 'INTEGER_BOUNDS':'A number is outside its allowed range (e.g. `--max-containers` 0-500).',
 'TEXT_BOUNDS':'A text field is empty, too long or contains control characters.',
 'NEW_EXTERNAL_RECEIPT_REQUIRED':'`interop-fingerprint --out` must be a new file outside the Forge package and not the input.',
 'NETWORK_UNAVAILABLE':'GitHub could not be reached. `run` needs network access (optionally set FORGE_GITHUB_TOKEN).',
 'AUTH_REQUIRED':'The local console request lacked the session token from the console URL.',
 'FROZEN_REUSE_TRIAL_CHANGED':'`contracts/reuse_trial.json` was edited; it is hash-pinned test data.',
 'TOOL_EXIT_':'Raised as `TOOL_EXIT_<n>`: a bounded helper subprocess exited with status n.',
 'FIXED_TRIAL_CONTRACT_REQUIRED':'The built-in reuse trial only runs against its own fixed contract.',
}
WORDS=[('CHANGED','A pinned input changed after it was recorded (hash/identity no longer matches). Start a new run/home, or restore the original file.'),
 ('MISMATCH','Recorded hash, size or identity does not match what was read.'),
 ('BINDING','A stored record is bound to different inputs/code than the current ones (integrity check).'),
 ('CHECKPOINT','A resumable checkpoint is invalid or belongs to another run; use a new run id.'),
 ('RECOVERY','Crash-recovery journal is inconsistent; use a new run id or home.'),
 ('DUPLICATE','The input repeats an item that must be unique.'),
 ('LIMIT','A safety size/count limit was exceeded.'),('BOUNDS','A value is outside its allowed size or range.'),
 ('COUNT','Wrong number of items for this field.'),('FIELDS','A JSON object has missing or unexpected fields.'),
 ('SCHEMA','Wrong or missing `schema` version.'),('REQUIRED','A required input or precondition is missing.'),
 ('AUTHORITY','The record claims an authority Forge never grants (e.g. release approval, execution).'),
 ('GITHUB_CORPUS','A GitHub-envelope corpus row or ZIP member failed validation.'),
 ('PACKET','A review/export packet failed integrity or shape validation.'),('RECEIPT','A receipt does not match its packet.'),
 ('SEARCH','Invalid search query, filter or index.'),('QUERY','Invalid query text or terms.'),
 ('TOOL','A bounded helper subprocess failed, timed out or exceeded output limits.'),
 ('TRIAL','The fixed contract/reuse trial inputs or outputs did not validate.'),
 ('EVIDENCE','An evidence citation is invalid, stale or points at the wrong target.'),
 ('OPPORTUNITY','An opportunity bundle, run or packet failed validation.'),('SOURCE','A source record or file failed validation.'),
 ('JSON','Malformed or oversized JSON.'),('MISSION','Mission record invalid or changed.'),
 ('CORPUS','Corpus run input or stored state failed validation.'),('REUSE','Reuse case input or state failed validation.')]
def guess(code):
    for w,t in WORDS:
        if w in code:return t
    return 'Input or stored state failed a validation check (see the raising module).'

def collect():
    found=defaultdict(set)
    for p in sorted(ROOT.rglob('*.py')):
        rel=p.relative_to(ROOT)
        if set(rel.parts)&SKIP or any(x.startswith('.') for x in rel.parts):continue
        for c in PAT.findall(p.read_text(encoding='utf-8',errors='replace')):found[c].add(rel.as_posix())
    return found

def render(found):
    cli={c:f for c,f in found.items() if any(x=='forge.py' or x.startswith('forge_core/') for x in f)}
    rest={c:f for c,f in found.items() if c not in cli}
    L=['# Forge error codes','',
       'When Forge refuses to do something it prints `{"status": "BLOCKED", "reason": "CODE", ...}` and exits with status 2.',
       'Add `--human` to any command for a readable version. Refusals are deliberate safety checks: Forge never',
       'overwrites outputs, never executes analysed code, and pins every input by hash.','',
       'If `reason` is a Python exception name instead of a code:','',
       '| reason | meaning |','|---|---|',
       '| `FileNotFoundError` | An input file or folder does not exist; the JSON `file` field names it. |',
       '| `PermissionError` | Forge cannot read/write that path. |','| `IsADirectoryError` / `NotADirectoryError` | A file was given where a folder is needed, or the reverse. |',
       '| `KeyError` / `ValueError` | Input JSON is missing an expected key or has a wrong value type. |','',
       '## Workbench (`forge.py`, `forge_core/`)','','| code | meaning | raised in |','|---|---|---|']
    for c in sorted(cli):
        L.append(f'| <a id="{c.lower()}"></a>`{c}` | {KNOWN.get(c) or guess(c)} | {", ".join(sorted(cli[c]))} |')
    L+=['','## Other components (Hound, loop, research runner, tools, tests)','','| code | meaning | raised in |','|---|---|---|']
    for c in sorted(rest):
        L.append(f'| <a id="{c.lower()}"></a>`{c}` | {KNOWN.get(c) or guess(c)} | {", ".join(sorted(rest[c]))} |')
    L+=['','Generated by `python3 tools/gen_error_codes.py`; hand-written meanings live in that script.','']
    return '\n'.join(L)

if __name__=='__main__':
    out=ROOT/'docs/ERROR_CODES.md';found=collect()
    if '--check' in sys.argv:
        have=out.read_text(encoding='utf-8') if out.exists() else ''
        missing=[c for c in found if f'`{c}`' not in have]
        print('missing: '+', '.join(sorted(missing)) if missing else 'ok');sys.exit(1 if missing else 0)
    out.write_text(render(found),encoding='utf-8');print(f'{len(found)} codes -> {out.relative_to(ROOT)}')
