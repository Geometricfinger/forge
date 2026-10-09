# Public demo pack (synthetic data only)

| Path | What it is |
|---|---|
| `src/sinks.py` | A small fake module full of risky call sites (pickle, subprocess, eval, tar/zip extraction, SQL string building, XML parsing). It is **scanned, never executed**. |
| `profile.json` | A data-only exact-API profile telling Hound which calls to report. |
| `manifest.json` | The source manifest (path, size, SHA-256, `fixture://` identity) required by `corpus-run`. |
| `opportunity_sources/` | Fake modules used as the code corpus for the opportunity case study (`forge.py opportunity-demo`). |
| `run_demo.py` | Runs everything end to end in a new, separate workspace. |

```sh
python3 examples/demo/run_demo.py --out /tmp/forge-demo
```

Or step by step:

```sh
python3 forge.py --home /tmp/forge-home init
python3 forge.py --home /tmp/forge-home corpus-run --source-root examples/demo/src \
    --manifest examples/demo/manifest.json --run-id demo --profile examples/demo/profile.json
python3 forge.py --home /tmp/forge-home corpus-search --run-id demo --query "pickle load"
python3 forge.py --home /tmp/forge-home opportunity-demo
python3 forge.py --home /tmp/forge-home serve      # local web UI on 127.0.0.1
```

To scan your own code, copy this layout: put files under a source folder, list each one in a manifest with its exact size and SHA-256, and point `--profile` at the APIs you care about. If you edit `sinks.py`, update its size and hash in `manifest.json`.
