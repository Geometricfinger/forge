# Forge exact-API probe add-on

A small add-on around Hound used by the Workbench and the improvement loop. Given exact source bytes and a data-only profile of target APIs, `tool/mission_probe.py` runs Hound's LocalFlow analysis and returns source-bound declared-import call observations. It validates that the configured Hound files match `contracts/hound_binding.json` before loading them, and never imports or runs the inspected code.

| Path | Purpose |
|---|---|
| `tool/mission_probe.py` | The probe used by the Workbench's isolated worker. |
| `tool/run_supervised_hunt.py` | Runs several data-only profiles over a manifest of sources. |
| `tool/test_addon.py` | Add-on test suite (69 tests). |
| `profiles/` | Example profiles: understanding, reliability, evaluation. |
| `contracts/` | Fixed challenge cases, the Hound binding and source manifests. |
| `run_scope_benchmark.py`, `reference/` | Compares per-source scope-cache reuse against the earlier probe version. |

```sh
python3 tool/test_addon.py --hound ../hound --out /tmp/addon-tests
python3 run_scope_benchmark.py --hound ../hound --out /tmp/scope-benchmark
```

Results are declared-import call observations, not runtime or correctness claims.
