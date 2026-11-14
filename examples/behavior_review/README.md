# Behavioral review example

This example indexes one synthetic Python source and compares illustrative
observations with a source-bound contract. Neither the component nor a supplied
harness is executed. The records are examples of passing, failing and incomplete
evidence, not claims that a real component passed runtime tests.

Use a new output folder outside the checkout and a physical path without symlinks:

```sh
mkdir -p "$HOME/forge-work"
WORK="$(cd "$HOME/forge-work" && pwd -P)"
python3 examples/behavior_review/run_demo.py --out "$WORK/behavior-review"
```

The command creates an existing corpus workspace at `behavior-review/home` and
an example under `behavior-review/example`. Expected summary:

```json
{
  "status": "BEHAVIORAL_REVIEW_EXAMPLE_PASSED",
  "results": {
    "pass": "REPORTED_PASS",
    "fail": "REPORTED_FAIL",
    "incomplete": "REPORTED_INCOMPLETE"
  },
  "same_contract_for_all_outcomes": true,
  "source_code_executed": false
}
```

Each report contains four cases. Two positive cases retain a valid score, and two
negative cases reject invalid values. A failing record reports an out-of-range
accepted score; an incomplete record skips one case. All three use the same fixed
contract. Output report hashes are held in evidence, so they need not be known
when the contract is created.

Inspect the generated `plan.json`, completed `contract.json`, `result-template.json`,
three evidence records and three review reports. `source/component.py` is the
original indexed source; `artifacts/component.py` is an exact byte copy checked
against that lead. `artifacts/` also holds the protocol and external report examples.
The example records the component execution as substituted and application
integration as unexecuted.

## Reproduce a review through the CLI

```sh
EXAMPLE="$WORK/behavior-review/example"
python3 forge.py --home "$WORK/behavior-review/home" opportunity-review \
  --bundle "$EXAMPLE/bundle.json" --registry "$EXAMPLE/registry.json" \
  --corpus-run behavior-example --behavior-contract "$EXAMPLE/contract.json" \
  --behavior-evidence "$EXAMPLE/pass-evidence.json" \
  --artifact-root "$EXAMPLE/artifacts" --out "$EXAMPLE/cli-pass-review.json"
```

Use `fail-evidence.json` or `incomplete-evidence.json` with a fresh output filename
to compare the other outcomes. Exit 0 means the comparison completed; inspect
`behavioral_review.status`. Invalid artifacts or identities return exit 2.

The existing Opportunity Lab and transfer results stay intact. Every behavioral
result retains false execution authentication and false reuse/release authority.
See [the full guide](../../docs/BEHAVIORAL_EVIDENCE.md) for the operator workflow,
schema rules and unverified execution/report-translation boundaries.
