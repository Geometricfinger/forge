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

### Bind the case's input files

When a case uses input files, prepare them before building and add an optional
`inputs` list to that case. Each reference identifies the exact bytes relative to
the `--artifact-root` used later for assessment:

```json
{
  "inputs": [
    {
      "path": "inputs/six.txt",
      "bytes": 2,
      "sha256": "<SHA-256 of the actual two bytes: 6 followed by a newline>"
    }
  ]
}
```

This is a case-field excerpt. Measure the actual file size and hash; replace the
placeholder before building. The build validates these declarations and retains
them inside the recipe and receipt. It does not read the input files or run them.
Assessment reads and checks every declared input, including inputs for cases
whose observations are missing, `NOT_RUN` or `ERROR`. Missing or changed declared
input files block assessment before publication. Intact inputs without an
observation still leave that case incomplete.

An `inputs` list must contain 1 to 16 strict `path`, `bytes`, `sha256` references.
Paths are normalized relative paths, without traversal, backslashes or a drive
prefix. Duplicate paths within a case and conflicting declarations of one path
across cases are refused. Each input or observation artifact is at most 1 MB;
their combined unique retained bytes are at most 8 MB. A shared file declared by
several cases counts once. Input files are opaque bytes: CSV, JSON, binary data
and source text are retained without importing or executing them.

Every assessed case records `input_status` as
`VERIFIED_DECLARED_INPUT_BYTES` or `NOT_DECLARED`. Older plans without `inputs`
retain their existing outcome behavior and explicitly report `NOT_DECLARED`.
The assessment always records `execution_input_usage_authenticated: false`:
matching retained input bytes does not prove the external product consumed them.

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
values, verified artifact identities, declared input bytes when supplied, and
remaining work. It does not modify the
build receipt's `runtime_validation: NOT_RUN`: packaging and assessment are
different events.

Matching hashes show consistency among the supplied records. They do not prove
when expectations were written, authenticate an external execution, verify that
an observation adapter interpreted the raw output correctly, or establish that
the cases fully represent a practitioner's needs. Review that interpretation and
compare the prototype with the existing workflow before claiming useful advantage.

## Use shared cases for a workflow comparison

For a baseline and a prototype, freeze a common set of task inputs and semantic
expected outcomes derived from the user's needs. Declare those input artifacts
in each applicable evaluation plan and retain each tool's raw outputs and reviewed
observation adapter. Matching input references can establish that the same task
material was retained for both arms; execution and its use of those files remain
external claims. If an adapter converts the common material into a tool-specific
input format, retain that conversion and the resulting native input too.

Keep implementation-specific checks, such as one product's export layout, return
codes or provenance fields, separate from the common comparison. Declare units,
time boundaries and precision before interpreting results. A daily estimate and
a continuous-time interval answer different questions unless the comparison
explicitly brings them to the same granularity. Preserve missing coverage and
unfavorable outcomes. Software runtime alone does not measure operator effort,
practical usefulness or adoption.
