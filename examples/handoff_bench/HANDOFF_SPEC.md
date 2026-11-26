# Handoff bench — export adapters and separate burden

This reviewed executable example extends the original evidence-recovery product.
The original `planner_core.py`, `SPEC.md`, manual expectations and selected
third-party module bytes are preserved. The optimizer objective, validation,
unknown-answer rule and question costs do not change.

## Input

`product.pyz --input PATH --mode handoff [--out NEW_DIRECTORY]` reads one bounded
UTF-8 handoff JSON object. Required keys are `schema` (1), `case_id`, `source`,
`raw_artifacts`, `reviewer_decisions`, `questions`, `budget`, `burden_events`, and
`follow_up`. Source is `manual`, `medit_export`, or `3shape_native`.

Each reviewer decision is an explicit supplied history with `id`, `decision`,
`answers`, and `conditions`. Removing `conditions` gives the original planner's
world object exactly. Conditions map field identifiers to opaque string values.
Every question has the original `id`, `label`, `cost`, and `available`, plus a
nonempty `source` description. Removing `source` gives the original question
exactly. No adapter supplies a new history, decision, question, answer or cost.

Each raw-artifact reference has `id`, `path`, `bytes`, `sha256`, `kind`,
`provenance`, and `selection`. Paths are relative to the input's directory.
References are pinned to regular files; absolute/traversing paths, symbolic
links, changed bytes, duplicate identities and oversize data are refused.
Provenance is `synthetic` or `reviewer_supplied`. It is a declaration, not an
authentication result. Selection is empty except for `medit_csv`, which requires
an explicit `case_name` string. Case names are labels, not globally unique IDs.

Kinds are `medit_csv`, `case_information_pdf`, `exocad_project_manifest`,
`native_package_manifest`, `opaque_native`, `scan_stl`, `scan_ply`, and
`reviewer_note`. The latter can hold a separately reviewed quotation or a
recorded follow-up response. The parser does not execute or fetch an artifact.

## Extraction boundary

The Medit CSV profile recognizes only the eight documented display labels in
`SOURCE_FIELDS.json`. It preserves nonempty matching cells as opaque strings.
This is an explicit bench profile: installed-export dialect, localization and
wire compatibility remain unverified. Missing headers/cells and undocumented
fields are `UNKNOWN`; differing nonempty matching observations are `CONFLICT`.
Unknown never becomes a different known value. Dates and form information are
not interpreted. The explicit Case Name selector must match a supplied row.

PDF and dentalProject artifact bytes are retained, but their undocumented
internal administrative layouts are not decoded. Their presence alone does not
prove that a particular administrative field survived.

A native input is a reviewer-created manifest, not a claimed proprietary
3oxz decoder. Its exact keys are `schema`, `case_id`, `manifest_kind`
(`reviewed_native_case`), `declared_format` (`3oxz`, `stl`, or `ply`), `provenance`,
`package_artifact`, and `observations`. Each observation has `category`,
`artifact_id`, `start`, `end`, and `quote`. Its character range and exact quote
must match the referenced UTF-8 reviewer artifact. Supported documented
categories are `order_or_case`, `scans`, `screenshots`,
`printable_order_forms`, and `patient_information`. The quote must be a strict
JSON object with exactly `category` (matching the manifest) and `value`
(`present`, `absent`, or `unknown`). The original frozen synthetic fixture also
has a deliberately narrow text profile: `Synthetic reviewer observation: FIELD
content is present.` with FIELD replaced by the exact category. That grammar is
admitted only when the manifest, package and note all declare synthetic origin.
No arbitrary or negative sentence is interpreted as presence. A valid positive
observation supports only the opaque value `present`; absent/unknown claims
remain unknown. Unobserved or undocumented categories remain
unknown; missing is not inferred absence. Source content interpretation remains
the reviewer's responsibility and its burden is retained.

The package reference must designate an opaque native artifact. STL/PLY-only
inputs and scan references stay scan-only even under a native source claim.
Neither a file extension nor a declaration authenticates a native format.
`native_package_decoded` and `vendor_format_verified` remain false.

## Model construction and outcomes

Validate the full reviewer model using the unchanged planner before filtering.
Retain a history unless both an extracted field and its supplied condition are
known and differ. Missing observations and missing conditions retain histories.
This retention is the only branching operation: no Cartesian product or
scan-derived history is generated. Existing question/answer bytes are copied.

An extraction conflict blocks the export-derived model. Zero surviving supplied
histories is `EXPORT_MODEL_CONFLICT`. Neither case calls the planner with a
fabricated world. Diagnostic supplied candidates remain available, and the
original manual model is still evaluated independently by the same planner.
For a nonempty consistent set, produce the original `{schema:1,cases:[...]}`
input shape and call the original validate/solve functions.

The export-only route resolves a decision only when all remaining histories
agree. A question recommendation does not resolve a case. The output compares
`manual_model`, `export_reconciliation`, and `export_plus_planner` on the same
supplied problem. Unresolved count is 0 or 1 per route. Independent declared
fixture truth stays in the evaluation expectations, outside the product input.
The product makes no truth/clinical-quality claim.

Optional follow-up records have `question_id`, `answer`, `artifact_id`, `start`,
`end`, `quote`, and `event_id`. They must cite a pinned reviewer note and a
follow-up burden event. Only supplied available questions can appear. Recorded
quotes must be strict JSON objects with exactly `question_id` and `answer`, equal
to the declared response; arbitrary prose does not authenticate an answer value.
Recorded
answers can remove a history only when its supplied answer is known and differs.
An unknown historical answer remains possible. Actual and synthetic follow-ups
are counted separately; planned questions are never counted as asked. The bench
does not send a request. Contradictory answers remain a conflict.

## Burden ledger

Input events have `id`, `stage`, `actor`, `measurement`, `wall_time_ns`,
`step_count`, and `source`. Stages are acquisition, interpretation,
model_preparation, and follow_up. Actor is human or adapter. Measurement is
observed, reported, synthetic, or unknown. Duration is integer nanoseconds, or
null for unknown. Sources describe the observation; they do not authenticate it.
Input step counts are positive integers. Runtime adds measured adapter events
and zero-step unknown placeholders for omitted necessary human stages.
An optional `routes` list allocates an event to manual_model,
export_reconciliation, and/or export_plus_planner. Omission declares a shared
charge. Each route sum includes an applicable shared event once; the global sum
includes every actual recorded event once. Allocation does not constitute an
independently observed counterfactual workflow.

`runtime_export_model` measures filtering, model construction and validation
shared by both export routes. For a consistent model, `runtime_export_plan`
separately measures the unchanged planner computation and is charged only to
`export_plus_planner`; no such event is emitted for a conflicting model.
Planner-compute duration is ledger wall time, separate from the unchanged
declared question-cost objective.

The three human stages acquisition, interpretation and model preparation are
always accounted for. Reading files locally measures local adapter I/O only;
it does not measure creating a vendor export or authoring a model. A missing
human duration remains null. Phase and total sums retain measured adapter,
observed human, reported and synthetic duration categories separately.
`known_wall_time_ns` sums supplied non-null values; it is not a claim that all
those values were measured. `wall_time_ns` is null if any necessary duration is
unknown. `complete` means numeric coverage; `observed_complete` additionally
requires observed provenance throughout. A fully synthetic sum is not observed
reviewer time. Sequential event sums are work burden, not a promise of elapsed
makespan for overlapping real work.

The ledger also reports recorded/synthetic questions asked and per-route
unresolved counts. Planner synthetic cost remains separate and unchanged.
No automatic inference of human savings or route winner is allowed from missing
timing, synthetic timing, or a proposed unanswered plan.

## Output and verification

Stdout is `{plan: ..., burden: ...}`. A new output directory contains exact
`plan.json` and `burden.json` sidecars and a byte-bound receipt. All output sizes
are checked before publication; existing output directories are refused.
Without `--out`, handoff mode uses `handoff-results` in the current directory.
Malformed inputs fail with a controlled code and do not publish partial plans.

Bounds: 262144 input bytes; 12 artifacts, at most 1048576 bytes each and
4194304 bytes combined; 32 observed/condition fields; 64 burden events;
32 native observations; the original 24 histories and 16 questions. The complete
stdout object is limited to 240000 bytes before publication. No arbitrary ZIP
extraction, PDF rendering, native plugin execution, network access or model call
is performed.

The same external resource supervisor and shared data-only verifier cover the
legacy batch and every handoff. Trust anchors are frozen before execution and
supplied independently to verification. Verify product/core/vendor/spec/input
pins, raw observations, exact commands and resource limits, before/after file
states, stdout/sidecar equality, semantic expectations and ledger arithmetic.
Unchanged mathematical output does not excuse changed identities or limits.
Hashes establish local consistency, not authenticated execution or chronology.

Only synthetic fixtures are included. Passing them qualifies this bench's
declared interchange and accounting behavior; it establishes neither genuine
vendor-binary compatibility nor a market advantage.
