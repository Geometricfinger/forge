# Assess a creation quest

`quest-build` records what was packaged. `quest-assess` creates a separate record
of what externally recorded observations say about that product's acceptance
checks and original needs. It reads data and artifacts without running the
product, checker or any supplied code.

## Define observable cases before building

Add an optional `evaluation_plan` to a completed creation recipe. Each case has a
unique ID, the zero-based index of one entry in the recipe's `acceptance` list,
the promised needs it exercises, and an expected JSON observation.

```json
{
  "evaluation_plan": {
    "schema": 1,
    "cases": [
      {
        "id": "double_six",
        "acceptance": 0,
        "needs": [],
        "expected": {"value": 12}
      }
    ]
  }
}
```

This is an addition to the existing recipe, not a complete recipe. For an idea or
scout quest, use an empty `needs` list. For an opportunity quest, use need IDs
from the recipe's `need_responses`. A case may exercise several promised needs.
A need the builder did not promise to address remains unaddressed.

Use cases that test observable outputs and important failure behavior. Select
expected values independently of the product's result. A file containing a
single claimed `passed: true` value provides little evidence about the user's
task; retain the actual relevant output instead.

Build using the existing command and bind the reviewed external checker:

```sh
python3 forge.py --home "$WORK/home" quest-build \
  --quest "$WORK/quest/quest.json" --recipe "$WORK/recipe.json" \
  --entry "$WORK/main.py" --source-root "$SOURCE" \
  --check-file "$WORK/check.py" --out "$WORK/product"
```

The plan is retained in `receipt.json`. Existing recipes without a plan continue
to build, but cannot yield evaluated case success without a plan.

## Record observations from the exact archive

Run the reviewed product and checker separately when authorized. Keep original
inputs and raw outputs. Each observed case references a bounded JSON artifact.
Forge compares the complete parsed JSON value with the case's expected value.
Equality is type-sensitive: `true`, `1` and `1.0` are distinct.

The result envelope has this shape; replace the placeholders with the digests and
byte count of the actual files:

```json
{
  "schema": 1,
  "receipt_sha256": "<digest of original receipt bytes>",
  "product_sha256": "<digest of product.pyz bytes>",
  "evaluation_plan_sha256": "<digest of canonical plan JSON>",
  "checker_sha256": "<digest of the checker bytes, or null when unbound>",
  "cases": [
    {
      "id": "double_six",
      "status": "OBSERVED",
      "artifact": {
        "path": "double_six.json",
        "bytes": 13,
        "sha256": "<digest of the actual observation file bytes>"
      }
    }
  ]
}
```

The example observation is `{"value":12}` followed by a newline, for 13 bytes;
always measure the file rather than copying an illustrative byte count. The
canonical plan encoding is UTF-8 JSON with sorted keys, compact separators,
ASCII escapes and no nonfinite numbers. Python's equivalent is:

```python
json.dumps(plan, sort_keys=True, separators=(",", ":"),
           ensure_ascii=True, allow_nan=False).encode()
```

Use `NOT_RUN` with `artifact: null` when a case was not performed. Use `ERROR`
when execution failed; a raw error artifact can be retained. Omitting a required
case leaves it incomplete. Duplicate or unknown case IDs are refused.

## Assess without changing the build

```sh
python3 forge.py --home "$WORK/home" quest-assess \
  --receipt "$WORK/product/receipt.json" --recipe "$WORK/recipe.json" \
  --product "$WORK/product/product.pyz" --check-file "$WORK/check.py" \
  --results "$WORK/results.json" --artifact-root "$WORK/observations" \
  --out "$WORK/assessment"
```

Use a new output directory separate from the checkout, workspace and input
artifacts. The assessment verifies the exact recipe against the build receipt,
the package and bound checker, and each referenced observation artifact. Results
from another package, receipt, plan or checker are refused.

Read the assessment's per-case, per-acceptance and per-need results. All mapped
cases must pass before a need can have a reported pass. An untested acceptance
check or promised need remains incomplete; an unaddressed need remains separate.
A per-need pass refers to its explicitly mapped cases; the complete practical
need can require evidence beyond those cases.
A failure calls for revision, incomplete coverage calls for completing the
evaluation, and passing selected outcomes still require reviewing remaining needs
or a field trial. These decisions describe follow-up work, not release approval.

## What the record establishes

The assessment preserves the original purpose and evidence, expected and observed
values, verified artifact identities and remaining work. It does not modify the
build receipt's `runtime_validation: NOT_RUN`: packaging and assessment are
different events.

Matching hashes show consistency among the supplied records. They do not prove
when expectations were written, authenticate an external execution, verify that
an observation adapter interpreted the raw output correctly, or establish that
the cases fully represent a practitioner's needs. Review that interpretation and
compare the prototype with the existing workflow before claiming useful advantage.
