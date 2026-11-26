# Opportunity Discovery 0.8 — implementation scope

## The reasoning boundary

The goal is to investigate **why a workflow still requires avoidable work** before searching for code to build another application. This layer is a reproducible implementation of part of that process. It consumes reviewed structured inputs; it does not presently learn human preferences or infer complete workflows from arbitrary Internet pages.

Implemented chain: attributed source → reviewed step/requirement map → missing-handoff inference → evidence state → purpose/mechanism candidates → bounded mechanism composition → prerequisites/conditions/output-fit review → existing FORGE search → counterevidence plan → experiment plan → retained owner feedback and export.

## Modules

`forge_core/opportunities.py` owns input validation, exact citations, source grouping, gap inference, state adjudication, mechanism selection, corpus linkage and persistent records. `invention_options.py` composes bounded routes over reviewed mechanism contracts; `invention_view.py` renders their escaped static review. `opportunity_examples.py` holds synthetic examples and the explicitly attributed source case study. `opportunity_demo.py` connects the case study to two actual retained corpus reports. The command line and authenticated local HTTP endpoints use those same implementations. Presentation is in `assets/opportunities.html` and `assets/opportunities.js`.

No domain-specific source function is imported to generate an opportunity. Code retrieval returns source-bound metadata using the existing ranker. No evidence-source URI is followed automatically. No model or general command tool is exposed to input content.

## Reviewed input schema

Top-level fields: `schema`, `title`, `as_of`, `max_age_days`, `public_brief`, `sources`, `workflows`, `observations`, `mechanisms`, `preferences`. Unknown fields fail validation.

A source has identity, exact retained text, kind, public/private state, URI, observed date, origin group and declared derivation. `capture_method` distinguishes a verbatim excerpt from an attributed summary or synthetic fixture. A citation is a source ID, start/end text offsets and an exact matching quote. This checks text correspondence—not whether the quote entails the interpretation, describes a real product, or is truthful. Observed date is not necessarily a publication date or independent freshness proof.

A workflow declares actor, domain, purpose, initial information, ordered steps, and explicit needs. Each step requires fields and produces fields. A requirement unavailable from initial or earlier output information creates an inferred hypothesis. A later producer does not repair an earlier consumer's missing input. Explicit needs can record additional owner-reported difficulty; they do not automatically become verified demand.

An observation ties a typed statement to one need and cites evidence. Kinds include pain/manual/missing, supported, refutes, unknown and not_described. Scope must identify target, analogy or unspecified. An unrelated product does not automatically settle the target workflow. Code reviews, marketing text, research and synthetic fixtures cannot count as firsthand customer pain.

Mechanisms separate purpose from how it works. They declare addressed patterns, inputs, outputs, limitations, citations and code queries. Matching a pattern is not semantic proof. Missing prerequisites and a missing required output remain explicit. Exact terminology drives field comparison; automatic ontology alignment, hidden synonyms and arbitrary causal inference are not claimed.

Workflows may additionally declare `required_conditions`, up to 16 unique IDs.
Mechanisms may declare `role` (`existing_option`, `source_component`, `new_work`,
`manual_step` or `unclassified`) and up to 16 `conditions`. Each condition has
exactly `condition`, `status` and `evidence`; status is `supported`, `incompatible`
or `unknown`, and evidence uses the ordinary one-to-eight exact citations. An
omitted role remains unclassified. An omitted required-condition declaration
remains unknown. These annotations do not verify capability or approve reuse.
See [build options](INVENTION_OPTIONS.md) for the exact additions and examples.

## Evidence states

`SINGLE_SOURCE_PROBLEM` and `CORROBORATED_WORKFLOW_PROBLEM` describe qualifying, fresh, scoped reported evidence. Corroboration counts supplied origin groups, exact normalized text and declared derivation; it is not authenticated person identity or statistical independence.

`INFERRED_HANDOFF_GAP` describes a missing field in the supplied workflow map. `INFORMATION_GAP_ONLY` means descriptions do not establish coverage. `UNTESTED_GAP_HYPOTHESIS` is unresolved. `STALE_EVIDENCE_RECHECK` retains out-of-date sources for review without treating them as fresh support.

`EXISTING_OPTION_TO_VERIFY` directs evaluation of the existing option instead of another new-code pitch. `CONFLICT_REQUIRES_REVIEW` and `COUNTEREVIDENCE_REVIEW` preserve contrary evidence and pause build recommendations. None grants runtime, rights, market, novelty or release approval.

## Composed build options

Each new hypothesis retains `build_options`. Starting with fields declared
available before its target step, synthesis searches supporting mechanism
contracts for the exact required output. A route has ordered steps, input
origins, condition checks, role classification, source citations and new-work
obligations. Supporting mechanisms may belong to other pattern families.

Incompatible required conditions exclude a mechanism. Unknown conditions remain
visible. Existing-option, contrary/conflicting-evidence and stale-only states
gate synthesis. An already available target needs no new route. Missing
providers, unreachable mechanisms and unseeded dependency cycles stay visible;
these catalog-wide diagnostics can describe branches unused by retained options.

The search is bounded to four steps, 4,096 retained states and eight displayed
options. Coverage records search omissions and unassessed results. It retains
distinct inclusion-minimal mechanism sets found within the bounds, with role
class coverage in the shortlist. This is not a cost optimum, independently
verified semantic diversity, or evidence of novelty. Field composition assumes
information accumulates; destructive effects and probabilistic outcomes require
separate modeling and testing.

The complete reviewed mechanism catalog is retained for deterministic replay.
Option citations use source/range/digest locators backed by that catalog; input
quote correspondence is checked against the retained source text. Saved reports
are checked against recomputed declarations at review/export boundaries.
Historical reports without options remain readable without invented new results.
The live UI and static export show the same declared routes, limits and next
decision. All `implementation_tested` and approval claims remain false.

## Search and candidate context

Real corpus search runs against selected immutable metadata snapshots. Corpus digests must match before and after analysis. Repeated corpus/query combinations are reused within the evaluation. Primary candidates require full query-term coverage. An identifier-shaped query further requires the exact or qualified symbol suffix. Each search now considers up to 12 ranked results. Bounded `review_candidates` retain partial concept matches with source identities, rank and matched/missing terms so a reviewer can inspect them and refine the declared query. They never enter the primary `candidates` list automatically, and an exact-symbol request does not admit an unrelated partial symbol. Each mechanism retains at most 12 primary and 12 review candidates, with per-search coverage and omitted counts. Unknown retrieval totals remain unknown. This remains a bounded shortlist, not a full-recall promise or proof of useful behavior.

Opportunity quests continue to use the primary candidates. To use a partial
review lead, inspect the complete source and revise the reviewed mechanism query
in a new bundle, for example with an explicit `symbol:` query once the intended
function is known. Preserve the earlier report and the reason for that change.
Retrieval does not supply the missing business interpretation or authorize reuse.

Source ID, source hash, real definition ID when present, container, path, original range and index identity travel with a code candidate. A script record does not receive a fabricated function ID. A match does not authorize raw-code reacquisition, modification or execution. The safe no-match outcome is retained.

## Persistence, feedback and interoperability

Input and report are stored together under a content-derived run ID, with an additional payload checksum. A cooperative publication lock prevents simultaneous creation. Repeated identical input returns the existing result without changing its bytes. Same-user filesystem and supplied provenance remain trusted; this is not an external attestation service.

Feedback is content-addressed and cooperatively locked. It permits INVESTIGATE/REVISE/REJECT with an attributed reason. A busy publisher reports a controlled blocker for retry. The notes never alter evidence or approve a release. There is no trained preference model or automatic feedback optimization in this increment.

Export includes opportunity report, feedback, escaped HTML, compressed hypothesis records and a restricted reviewer task packet, plus a checksum manifest. Verification checks exact members and their bytes, report/run identity, request regeneration and feedback authority. A forged, internally consistent packet is not cryptographically authenticated; portable verification does not establish the reality of its observations.

## UI and API

Guided UI: enter a note, actor, domain, received information, required information, pattern and the observation you actually have. The default is unknown. Use an imported reviewed JSON brief for multi-step, multi-source work. The note reader highlights bounded lexical cues; it does not produce verified claims.

GET: `/api/opportunities`, `/api/opportunity?run=...`.
POST: `/api/opportunity/analyze`, `/api/opportunity/guided`, `/api/opportunity/draft`, `/api/opportunity/feedback`, `/api/opportunity/export`, `/api/opportunity/demo`.

The static Opportunity Lab is available at `/opportunities`; API operations require the existing loopback host/origin and bearer-token checks. There is no arbitrary file or command endpoint. The existing server is still not a hardened public service.

## Capacity and outstanding work

Limits include 384 KB input, 80 sources, 20 workflows, 30 steps per workflow, 40 mechanisms, 80 total generated/explicit hypotheses, four corpus selections, and bounded text/citation arrays. Broad collections should be partitioned with separate records; no automatic global census or cross-batch market inference exists. Local list views are bounded and not a distributed index.

The exact serialized saved record (input bundle, report and checksum) is limited
to 10,000,000 bytes. A larger record fails with `OPPORTUNITY_RECORD_BOUNDS` before
publication. Compact option citations retain source/range/quote-digest locators
that resolve through the shared `mechanism_catalog`; they avoid copying full
passages into every step while preserving the reviewed citation identity.

Unfinished: learned purpose/mechanism extraction, independent opportunity assessment, authorized large-scale product-listing acquisition, cross-language semantics, reliable demand evidence, actual generated-application evaluation, independent judge permissions, native Mac/Windows and production hosting. Preserved resource/authentication boundaries must not be removed to make these labels disappear.
