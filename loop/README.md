# Forge controlled improvement loop

A local controller around Hound and the exact-API probe add-on. It builds data-only search-profile candidates, evaluates them against a fixed contract and exact source snapshots, keeps failed work, and stops at "ready for adoption review". It never adopts a candidate, edits implementations or tests, or deploys anything.

## Run the demonstrated cycle

```bash
python3 run_demo.py --out /tmp/forge-loop-run
```

The output folder must be new and outside the repository. `prepare_runtime.py` copies the sibling `../addon` and `../hound` components into a separate runtime folder and verifies their `SHA256SUMS.txt` manifests. The demo then runs the Hound tests (483), the add-on tests (69) and the controller tests (72), and replays three supplied profile proposals:

| Profile | Correct fixed cases | Decision |
|---|---:|---|
| Original reliability profile | 9/16 | Baseline measured |
| Noisy proposal | 15/16 | Rejected: a required negative case regressed |
| Partial proposal | 12/16 | Improved, but incomplete |
| Complete proposal | 16/16 | Ready for adoption review; not adopted |

More matches are not automatically better: the noisy profile mislabels JSON serialization as hashing, and the evaluator rejects it despite more correct cases. The 16 cases are developer-authored fixtures, not independent held-out data.

## Interface

`forge_cycle.py` provides `init`, `run`, `status`, `request`, `submit` and `export`. Proposals are append-only, data-only API target lists; commands, plugin code, protected-field replacements and authority flags are rejected. Interrupted runs resume from task-level checkpoints (`run_demo.py --resume`); `--steps 1` pauses after one stage.

## Limits

Hashes, schemas and claim tokens are local consistency controls, not signatures or a hardened sandbox. The database, clock and output folder must be controlled by a trusted operator.
