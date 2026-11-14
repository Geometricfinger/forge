# Behavioral evidence for a selected source lead

FORGE can bind a proposed component to an explicit acceptance contract and compare
externally recorded observations with that contract. It recomputes the comparison
and checks selected artifact bytes. It does not run a supplied harness, authenticate
an external execution, or decide whether the operator chose the right assertions.

This extends the declared transfer review. `original_report`, `transfer_reviews`
and their authority fields remain unchanged; the additional result is under
`behavioral_review`. A recorded pass never upgrades the earlier declaration to a
runtime, rights, reuse or release approval.

## Working sequence

1. Index the authorized source in an existing workspace. Keep the source,
   workspace, artifact inputs and review outputs separate.
2. Run `opportunity-review` with a source-bound bundle and declaration registry,
   including the relevant `--corpus-run`. Select one current experiment candidate
   that has a declared direct or supporting role and an actual retrieved code lead.
3. Run `opportunity-plan` with the hypothesis, mechanism, corpus and candidate ID
   from that review. The result is an incomplete draft requiring operator input.
4. Complete the contract: objective, protocol, real/substituted/unexecuted scope,
   environment, limitations, exact source artifact and acceptance cases. Freeze it
   before candidate evaluation when making a prospective experiment claim.
5. Use `behavior-result-template` to create `NOT_RUN` rows. An authorized external
   evaluator can record outcomes and observations, retaining its original report.
   This command does not start that evaluator.
6. Supply the contract, recorded evidence and artifact root to `opportunity-review`.
   Inspect the behavioral status, case results, omissions and authority fields.

The [synthetic example](../examples/behavior_review/README.md) provides a complete,
replayable instance of this sequence. Its observations are deliberately constructed
data, so no component execution is implied.

The [reviewed evaluator](REVIEWED_EVALUATOR.md) supplies a separate, named path for
the two bundled RFC 8785 subjects. It freezes their contracts before execution
and validates translation from the fixed raw report. This does not add execution
to `opportunity-review` or grant that command authority over an external harness.

## CLI

The commands use an existing `--home` containing the selected corpus. IDs below
come from the current transfer review; a definition ID is preferred, with a source
ID used for a module-scope lead that has no definition ID.

```sh
python3 forge.py --home "$WORK/home" opportunity-plan \
  --bundle "$WORK/bundle.json" --registry "$WORK/registry.json" \
  --corpus-run my-source --hypothesis "$HYPOTHESIS" \
  --mechanism "$MECHANISM" --corpus my-source --candidate "$CANDIDATE" \
  --out "$WORK/plan.json"

# Complete plan.contract and save it as contract.json before evaluating a candidate.
python3 forge.py --home "$WORK/home" behavior-result-template \
  --contract "$WORK/contract.json" --out "$WORK/result-template.json"

python3 forge.py --home "$WORK/home" opportunity-review \
  --bundle "$WORK/bundle.json" --registry "$WORK/registry.json" \
  --corpus-run my-source --behavior-contract "$WORK/contract.json" \
  --behavior-evidence "$WORK/evidence.json" --artifact-root "$WORK/artifacts" \
  --out "$WORK/review.json"
```

Omit `--behavior-evidence` to check the completed contract and its input artifacts
without reporting results. Without any behavioral flags, `opportunity-review`
retains its declaration-only behavior.

Outputs must be new files outside the checkout, the selected corpora's original
source folders, the artifact input root, and the workspace `engine` and `corpora`
directories. Existing files and symlink paths are refused. The artifact root and
workspace cannot contain one another. Invalid supplied JSON or a missing corpus
does not trigger automatic workspace initialization for these behavioral commands.

## Contract and artifact identity

The draft retains the full current code lead, its digest, and the digest of the
complete transfer review. Evaluation recomputes that review and requires an exact
match for the hypothesis, workflow, need, mechanism, role and lead. An unrelated
passing result cannot be attached to another component.

`tested_subject.subject_artifact` names a file in
`tested_subject.artifacts`. Its SHA-256 must equal the indexed lead's source hash.
If a candidate changes, index that candidate as a new source and create its own
plan. Updating an artifact manifest alone cannot change the selected source.
For an extracted notebook/archive/text segment, retain the exact source bytes
identified by the lead, including the original source view where applicable.

Each artifact row contains a normalized relative `path`, byte `size` and `sha256`.
FORGE reads the selected local files and checks their actual bytes. The protocol's
specification, harness and fixture-manifest hashes must each refer to a nonempty
declared input artifact. Dependencies may also be listed, but their completeness
is not established. Unlisted files in the artifact root are not scanned.

Keep input artifacts in the contract and later output artifacts in
`evidence.artifacts`. The evidence's `report_sha256` must name a nonempty output
artifact. A report may include the contract digest without creating a circular
dependency. The same frozen contract can assess passing, failing and incomplete
reports without changing its acceptance identity.

JSON identity uses `forge_core.common.canonical`, then SHA-256. Formatting a JSON
file differently does not change its canonical identity. Artifact identities,
however, always cover the exact file bytes. Hashes establish local consistency;
they do not authenticate who created a report or when acceptance was frozen.

## Acceptance cases and observations

A completed contract has 2 to 256 cases, including at least one `positive` and one
`negative` case; `regression` is also available. Each case has a unique ID, declared
input hash and 1 to 16 assertions. Positive controls should preserve useful behavior
so that a component which rejects everything cannot appear successful.

The two assertion operations are deliberately small:

| Operation | Fields | Comparison |
|---|---|---|
| `equal` | `id`, `field`, `op`, `expected` | Canonical JSON equality, including nested data. `true`, `1` and `1.0` are distinct. |
| `finite_range` | `id`, `field`, `op`, `minimum`, `maximum` | A finite JSON number within inclusive bounds. Booleans are not numbers. |

`field` names a direct entry in a case's observations map. It is not an expression,
attribute path or callable. There is no scripting, callback, import or command
field. A source file and a harness are treated as bytes only.

Evidence rows have `id`, `outcome`, `observations` and `detail`. Outcomes are
`OBSERVED`, `ERROR`, `SKIPPED` or `NOT_RUN`. Non-observed outcomes require empty
observations and a nonempty explanation. There is no trusted per-case pass flag
or trusted summary total. Duplicate or unknown case IDs are refused; absent
required cases remain visible as missing.

The template intentionally lacks reporter/report/artifact information. Fill those
fields with a matching external report before treating it as completed evidence.

## Status and exit behavior

| `behavioral_review.status` | Meaning |
|---|---|
| `PLAN_ONLY_NOT_EXECUTED` | Contract and input artifact checks completed; no evidence was supplied. |
| `REPORTED_PASS` | Every required case was observed and every assertion agrees with the supplied observations. |
| `REPORTED_FAIL` | At least one assertion disagrees or a case reports `ERROR`. Missing work remains listed independently. |
| `REPORTED_INCOMPLETE` | No failure was observed, but cases or required observation fields are missing, skipped or not run. |

Exit 0 means the comparison completed, including a failing or incomplete result.
It is not a release gate. A caller must inspect the status and case details.
Malformed inputs, stale identities, unreadable/mismatching artifacts and unsafe
output layouts return `BLOCKED` with exit 2.

## Bounds and remaining work

Contract and evidence JSON are each bounded at 1 MB, nesting depth 16, 100,000
nodes and bounded strings/collections. Across input and output manifests together,
at most 64 unique paths, 8 MB per file and 32 MB total are accepted. JSON NaN and
Infinity, unknown fields, duplicate paths and path traversal are refused.

The record explicitly leaves these claims unverified:

- The truth and usefulness of the operator's objective, declarations and assertions.
- Whether each declared input hash was correctly mapped into the fixture manifest.
- Whether the evidence observations faithfully translate the retained original report.
- Whether the external test ran against the reported source, environment and inputs.
- Whether the contract was frozen before seeing results.
- Dependency completeness, semantic suitability, licensing and application release readiness.

The implementation keeps runtime/execution authentication, independent evaluation,
reuse and release authority false even after `REPORTED_PASS`. A stronger integration
needs a separately reviewed evaluator with a fixed protocol, real positive and
negative controls, a trustworthy execution environment and an evidence translator
whose behavior is independently checked. This command does not silently provide one.
