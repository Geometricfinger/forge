# Corpus capacity and incomplete results

Forge bounds each source container, parser operation and aggregate report
separately. Many individually admissible files can still exceed a run's metadata
budget. Source bytes and definition metadata are different quantities: a short
function can produce many structural fields, provenance records and search terms.

## Publishing large reports

The report JSON, compressed capability records and review page are written in
chunks. Forge does not build a second complete JSON string, an uncompressed
capability cart or a complete HTML record list just to publish them. The JSON
schema and canonical byte encoding remain unchanged. The receipt hashes the
exact report and compressed cart bytes that were written.

Each file is replaced only after its complete temporary file has been flushed
and synchronized. The receipt is published after the other outputs. An interrupted
publication can leave checkpoints or an unreceipted report; readback must still
verify the receipt before accepting a report. This is not a multi-file filesystem
transaction or an execution attestation.

## Aggregate metadata budget

A run retains at most **160,000,000 canonical JSON bytes of source records**.
The full report also contains summaries and coverage, and its independent
publication/read ceiling remains **256,000,000 bytes**. These byte budgets do not
promise a fixed process memory footprint. Python objects, indexing and the review
page in a browser need additional memory.

When adding the next source would exceed the metadata budget:

- The completed source checkpoint is preserved.
- That source is recorded under `coverage.blocked` with
  `CORPUS_METADATA_BUDGET`; later containers are explicitly unread for the same
  reason.
- The accepted prefix is published with status `INCOMPLETE`, and definition
  discovery is marked incomplete. No missing definition count is invented.
- `coverage.metadata_budget` states the retained byte count and exact limit.

Split the manifest into smaller sets and use a new run ID for each. Repeating the
same over-budget selection does not remove the aggregate limit. Existing valid
checkpoints remain available for diagnosis and replay; neither the source files
nor source/parser size limits are changed.

If a fixed tool's I/O collector cannot allocate memory, it terminates the owned
process group and reports `TOOL_MEMORY_LIMIT`. A Python allocation failure that
reaches the command boundary reports `MEMORY_BUDGET_EXHAUSTED` when enough memory
remains to emit the small refusal. Neither outcome is a completed scan.

## What a completed structural scan means

Large modules may retain every definition while exceeding the API analyzer's
separate source-size limit. Such reports remain `COMPLETED_WITH_GAPS`: searchable
definitions do not imply complete API-call analysis. Check both the definition
coverage and the recorded gaps before using a result.

Literal symbol lookup traverses the saved definition metadata. Ranked search
also constructs an in-memory index and has its own record bound. A successful
scan or symbol lookup does not establish that every downstream operation fits
the same memory budget, or that the code can run correctly.
