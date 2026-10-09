# Hound: Forge's read-only static analyzer

Hound inspects Python source bytes and reports declared-import API call observations. It reports a call only when it can bind it back to a declared import, and deliberately refuses ambiguous, conditional or rebound imports. It is not a running AI worker, a whole-repository crawler, a numerical verifier or a release authority, and it never imports or executes the code it reads.

| Path | Purpose |
|---|---|
| `hound.py`, `baseline_sniffers.py`, `flow_core.py`, `library_rules.py`, `script_scope.py` | The analyzer: base detectors, local flow inference, reviewed library API mappings and loose-script scope observations. |
| `profiles/baseline.json` | The default target profile. |
| `atlas_hunt.py`, `persistent_hunt.py`, `mission_hunt.py`, `value_gate.py` | Catalog building, resumable hunts over many sources, mission queries and a value gate for candidates. |
| `build_public_catalog.py` | Builds a metadata-only catalog from a reviewed manifest of pinned public files (repository, commit and hashes). Source bodies are not retained. |
| `missions_public/` | Example missions for public catalogs. |
| `reference/` | Earlier Hound versions used by the regression comparisons. |
| `tests/`, `run_tests.py`, `run_qualification.py` | Regression suite (483 tests) and the qualification runner. |
| `skills/forge-hound/` | Usage guidance for an agent that queries Hound output. |

## Run the tests

```bash
python3 run_tests.py --out /tmp/hound-tests
python3 run_qualification.py --out /tmp/hound-qualification --rounds 2
```

Output folders must be new. Qualification success is `CLEAN_FOR_DECLARED_SCOPE_REVIEW_REQUIRED`, never release approval or proof of general correctness. No third-party packages, model downloads or network access are needed.

## Catalog pinned public sources

Write a manifest listing each file's repository, commit, path, SHA-256 and Git blob id, place the exact source bytes in a separate folder, then:

```bash
python3 build_public_catalog.py --manifest /approved/manifest.json \
  --source-root /approved/public-snapshot --out /new/public-atlas
python3 persistent_hunt.py --catalog /new/public-atlas/public_catalog.sqlite \
  --source-root /approved/public-snapshot --locators /new/public-atlas/source_locators.json \
  --out /new/public-hunt --max-new-sources 10 --workers 2
python3 mission_hunt.py --catalog /new/public-atlas/public_catalog.sqlite \
  --hunt /new/public-hunt/hunt.json --mission missions_public/linear-solver-candidates.json \
  --out /new/linear-solver-candidates.json
```

These tools verify exact source identity and never install or execute upstream packages.

## Limits

Rankings count observed matches; they are not confidence. Matching a declared import does not establish behavior, units, coordinate frames or correctness. The parser keeps byte, node, time and process limits, but is not a hostile-code isolation service.
