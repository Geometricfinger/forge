# Handoff bench

This reviewed executable example connects export evidence to the existing
fixed-batch evidence planner and records the work required to prepare its model.
The planner's source, behavioral specification, original 24 manual cases and
permitted component bytes are preserved. This example does not add an adaptive
tree or change the planner's objective.

The authoritative contracts are [HANDOFF_SPEC.md](HANDOFF_SPEC.md) for adapters
and burden, and the unchanged [SPEC.md](SPEC.md) for planning. The
[field source map](SOURCE_FIELDS.json) identifies the public documentation and
the gaps in format coverage.

## Build and run the same binary

Use Python 3.11 or newer from the repository root. Choose a new build directory
outside the repository and the input directory:

```sh
python3 examples/handoff_bench/build.py \
  --out /absolute/path/to/new-handoff-build
```

The build preserves the pinned planner and whole permitted dependency modules,
checks source identities and creates `product.pyz` without executing it.
Run a reviewed handoff input explicitly:

```sh
python3 /absolute/path/to/new-handoff-build/product.pyz \
  --input /absolute/path/to/handoff-case.json \
  --mode handoff \
  --out /absolute/path/to/new-handoff-result
```

The input references actual adjacent fixture/export files using relative paths,
exact sizes and SHA-256 hashes. No artifact is downloaded. Stdout contains both
`plan` and `burden`. With `--out`, the new directory receives `plan.json`,
`burden.json` and an integrity receipt. Existing output is refused and output
bounds are checked before publication.

The same binary accepts the original planner input without handoff mode:

```sh
python3 /absolute/path/to/new-handoff-build/product.pyz \
  --input examples/handoff_bench/inputs/manual-baseline/input-batch.json
```

The reviewed external supervisor and verifier retain execution observations.
They apply resource limits on a trusted host; they do not provide filesystem
or network isolation. Runtime claims require actual execution records, not
successful packaging alone.

## Run and verify the bounded benchmark

The runner uses one product and the same supervisor/verification contract for
the original 24 cases and five handoffs. To prepare the product and freeze inputs
without executing it:

```sh
python3 examples/handoff_bench/run_bench.py \
  --out /absolute/path/to/new-static-bench
```

To execute the exact reviewed product explicitly, choose a different new output:

```sh
python3 examples/handoff_bench/run_bench.py \
  --out /absolute/path/to/new-executed-bench \
  --execute-reviewed-product
```

The runner retains `benchmark.json`, the build, pre-execution frozen binding and
raw supervised observations. The data-only verifier can check that execution
again without rerunning the product:

```sh
python3 examples/handoff_bench/verify_run.py \
  --binding /absolute/path/to/new-executed-bench/frozen/binding.json \
  --run /absolute/path/to/new-executed-bench/execution \
  --trusted-freeze-sha256 EXACT_PREEXECUTION_HASH \
  --out /absolute/path/to/new-verification.json
```

Supply the trusted digest retained before execution. A checksum freshly learned
from an untrusted changed binding is not an independent trust anchor. These
checks establish internal byte/contract consistency, not authenticated history.

## What each adapter can read

| Input | Supported bench behavior | Limit |
| --- | --- | --- |
| Medit case-list CSV | Select an explicitly named case and preserve eight documented content items as opaque strings | This restricted English-header profile is not verified against every installed exporter, dialect or localization |
| Case-information PDF | Bind the supplied artifact bytes | No undocumented PDF field layout is decoded |
| exocad project export manifest | Bind the supplied artifact bytes and retain its limits | No dentalProject administrative schema is inferred |
| 3Shape native package manifest | Check a reviewer-created manifest and exact quoted observations of documented content categories | No proprietary 3oxz binary decoding or format authentication |
| STL/PLY | Retain scan-only evidence | No case metadata, history or implant identity is inferred from geometry |

The native manifest is this bench's reviewed interchange, not a vendor-defined
JSON format. Its observations must cite exact ranges in pinned reviewer
artifacts. Supported category observations establish only the opaque value
`present`. A missing category is unknown, not absent. A package extension
cannot establish authenticity, and STL/PLY-only input never becomes native
administrative evidence.

Only supplied histories, decisions, questions and answers enter the model.
A known export observation removes explicitly conflicting supplied histories.
An unknown field retains them. Contradictory observations or an empty surviving
model produce a review conflict rather than a fabricated history.

## Outcomes and burden

Each handoff compares the manual model, export reconciliation and export plus
planner for the same supplied problem. Reconciliation resolves a decision only
when the remaining histories agree. A suggested question set is a plan for
review; it does not count as questions asked or establish a decision.

The ledger separately records acquisition, interpretation, model preparation
and follow-up events, plus unresolved counts. It retains actor, measurement
class, duration, steps and source. Planner cost remains its original declared
synthetic quantity.

Measured adapter time, observed human time, reported time and synthetic time
remain separate. Missing necessary human timing keeps the complete total
unknown. Known subtotals do not establish total savings. Per-route ledgers
allocate shared work once per applicable alternative; this accounting does not
turn one run into independently timed executions of all alternatives.

A follow-up record must name an existing available question, cite a pinned
response and reference its burden event. Recorded and simulated follow-ups stay
separate. The product sends no messages.

## Existing report extension

`report.augment_report(existing_html, benchmark)` adds or replaces one static
handoff comparison section in an existing report. It introduces no new app,
screen, JavaScript or stylesheet. It shows route outcomes, independent
synthetic correctness labels when supplied, unresolved counts, planned versus
recorded follow-up and timing completeness. All new evidence text is escaped.

The separate verifier supplies correctness from frozen fixture expectations,
outside the product input. Missing truth or an unresolved decision stays
`UNKNOWN`; a successful optimizer computation is not relabeled a correct
real-world decision.

## What passing this bench means

Synthetic fixtures exercise documented content items, explicit reviewer
manifest contracts, model filtering, burden accounting and provenance
consistency. They do not establish compatibility with genuine native exports,
representative reviewer time, a clinical outcome or a market gap. Real,
permitted exports and measured operator work are needed before comparing the
full burden of competing workflows.

See [the engineering guide](../../docs/HANDOFF_BENCH.md) for the preserved
boundaries and verification rules.

