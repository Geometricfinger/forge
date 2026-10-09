# Opportunity Discovery 0.8 — implementation scope

## The reasoning boundary

The goal is to investigate **why a workflow still requires avoidable work** before searching for code to build another application. This layer is a reproducible implementation of part of that process. It consumes reviewed structured inputs; it does not presently learn human preferences or infer complete workflows from arbitrary Internet pages.

Implemented chain: attributed source → reviewed step/requirement map → missing-handoff inference → evidence state → purpose/mechanism candidates → prerequisites/output-fit check → existing FORGE search → counterevidence plan → experiment plan → retained owner feedback and export.

## Modules

`forge_core/opportunities.py` owns input validation, exact citations, source grouping, gap inference, state adjudication, mechanism selection, corpus linkage and persistent records. `opportunity_examples.py` holds synthetic examples and the explicitly attributed source case study. `opportunity_demo.py` connects the case study to two actual retained corpus reports. The command line and authenticated local HTTP endpoints use those same implementations. Presentation is in `assets/opportunities.html` and `assets/opportunities.js`.

No domain-specific source function is imported to generate an opportunity. Code retrieval returns source-bound metadata using the existing ranker. No evidence-source URI is followed automatically. No model or general command tool is exposed to input content.

## Reviewed input schema

Top-level fields: `schema`, `title`, `as_of`, `max_age_days`, `public_brief`, `sources`, `workflows`, `observations`, `mechanisms`, `preferences`. Unknown fields fail validation.

A source has identity, exact retained text, kind, public/private state, URI, observed date, origin group and declared derivation. `capture_method` distinguishes a verbatim excerpt from an attributed summary or synthetic fixture. A citation is a source ID, start/end text offsets and an exact matching quote. This checks text correspondence—not whether the quote entails the interpretation, describes a real product, or is truthful. Observed date is not necessarily a publication date or independent freshness proof.

A workflow declares actor, domain, purpose, initial information, ordered steps, and explicit needs. Each step requires fields and produces fields. A requirement unavailable from initial or earlier output information creates an inferred hypothesis. A later producer does not repair an earlier consumer's missing input. Explicit needs can record additional owner-reported difficulty; they do not automatically become verified demand.

An observation ties a typed statement to one need and cites evidence. Kinds include pain/manual/missing, supported, refutes, unknown and not_described. Scope must identify target, analogy or unspecified. An unrelated product does not automatically settle the target workflow. Code reviews, marketing text, research and synthetic fixtures cannot count as firsthand customer pain.

Mechanisms separate purpose from how it works. They declare addressed patterns, inputs, outputs, limitations, citations and code queries. Matching a pattern is not semantic proof. Missing prerequisites and a missing required output remain explicit. Exact terminology drives field comparison; automatic ontology alignment, hidden synonyms and arbitrary causal inference are not claimed.

## Evidence states

`SINGLE_SOURCE_PROBLEM` and `CORROBORATED_WORKFLOW_PROBLEM` describe qualifying, fresh, scoped reported evidence. Corroboration counts supplied origin groups, exact normalized text and declared derivation; it is not authenticated person identity or statistical independence.

`INFERRED_HANDOFF_GAP` describes a missing field in the supplied workflow map. `INFORMATION_GAP_ONLY` means descriptions do not establish coverage. `UNTESTED_GAP_HYPOTHESIS` is unresolved. `STALE_EVIDENCE_RECHECK` retains out-of-date sources for review without treating them as fresh support.

`EXISTING_OPTION_TO_VERIFY` directs evaluation of the existing option instead of another new-code pitch. `CONFLICT_REQUIRES_REVIEW` and `COUNTEREVIDENCE_REVIEW` preserve contrary evidence and pause build recommendations. None grants runtime, rights, market, novelty or release approval.

## Search and candidate context

Real corpus search runs against selected immutable metadata snapshots. Corpus digests must match before and after analysis. Repeated corpus/query combinations are reused within the evaluation. Primary candidates require full query-term coverage. An identifier-shaped query further requires the exact or qualified symbol suffix. Partial matches are counted separately, not represented as complete candidates. This is a precision-oriented shortlist; it can omit useful weaker matches and is not a full-recall promise.

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

Unfinished: learned purpose/mechanism extraction, independent opportunity assessment, authorized large-scale product-listing acquisition, cross-language semantics, reliable demand evidence, actual generated-application evaluation, independent judge permissions, native Mac/Windows and production hosting. Preserved resource/authentication boundaries must not be removed to make these labels disappear.
