# Reviewed RFC 8785 experiment

This example connects actual execution of two fixed, reviewed JSON serializers to
FORGE's source-bound behavioral review. It uses the existing 28 published examples
in `contracts/interop/`: 26 positive serialization cases and two nonfinite-number
rejections per subject. The baseline is `forge_core.common.canonical`; the selected
component is the pinned `forge_core._vendor.rfc8785` implementation. The examples
were already visible to the project and are not new or withheld evaluation data.

```sh
WORK="$(mktemp -d "${TMPDIR:-/tmp}/forge-reviewed.XXXXXXXX")"
WORK="$(cd "$WORK" && pwd -P)"
python3 -B examples/reviewed_evaluator/run_demo.py --out "$WORK/reviewed-trial"
```

Use a new output directory outside the checkout. The example creates separate
`home`, `prepared` and `result` directories and saves `SUMMARY.json`. Preparation
indexes each real source lead and freezes separate contracts, source and evaluator
bindings, fixture-to-case mappings, acceptance assertions and environment details.
The run uses only the fixed bundled trial. Its recorded outputs are translated
into behavioral observations and compared with the unchanged contracts.

The same phases are independently available in the CLI:

```sh
python3 -B forge.py --home "$WORK/home" reviewed-trial-prepare \
  --out "$WORK/prepared"

# This is the explicit execution step.
python3 -B forge.py --home "$WORK/home" reviewed-trial-run \
  --prepared "$WORK/prepared" --out "$WORK/result"

# Data-only verification; no workspace or component execution is needed.
python3 -B forge.py reviewed-trial-verify \
  --prepared "$WORK/prepared" --result "$WORK/result"
```

Preparation and verification do not evaluate the trial inputs. FORGE continues to
use its normal metadata serializer while building and checking records. Execution
does not accept a caller-selected module, callable, shell command or test harness.
It makes no network or model requests. The independent Node comparison and the
broader admission tests in the older `contract-trial` are outside this lane.

See [REVIEWED_EVALUATOR.md](../../docs/REVIEWED_EVALUATOR.md) for exact comparison
rules, recorded authority, failure handling and exit behavior.
