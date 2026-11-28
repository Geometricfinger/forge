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

## Internal checkpoint storage

New source and successful-segment checkpoints use explicit **`.json.gz`** files
in `checkpoints/` and `segments/`. Each contains one deterministic gzip member
(compression level 1) whose decoded value is the existing canonical JSON wrapper:
`binding`, `result`, and `result_sha256`. The result is encoded once for its
checksum, publication and source-metadata byte count. This changes internal
storage, not the public report, capability records, review-page record shape or
the capability cart's compression settings.

The checkpoint ceiling remains **20,000,000 bytes**. Both the stored file and
the decoded wrapper must fit it. Reads reject malformed or truncated gzip,
checksum failure, extra members and all trailing bytes. Strict JSON, binding,
result checksum, authority and typed coverage checks still apply after decoding.
New writes must fit before publication; compression cannot admit a larger decoded
checkpoint. As with reports, a temporary file is synchronized before replacement,
and an interrupted write leaves the previous file intact.

Existing raw **`.json`** checkpoints remain readable and are updated in their
original format when recovery is needed. No automatic migration or deletion is
performed. If both extensions exist for the same checkpoint, Forge refuses the
conflict with `CORPUS_CHECKPOINT_FORMAT_CONFLICT`, even when their decoded values
agree. Preserve both files for diagnosis and rebuild from the pinned sources in
a fresh run. A code upgrade still requires a fresh code-bound home; raw-format
compatibility does not override that rule.

Recovery revisions retain the exact original stored bytes and their matching
`.json` or `.json.gz` suffix. Successful sibling caches are not rewritten during
retry. Recovery journals and result digests continue to refer to canonical
decoded results; hashes of stored-file evidence cover its actual compressed or
raw bytes. These checks establish consistency and observed recovery, not execution
of the inspected source.

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

The complete-statement partition path can extend the original 2,048-unit prefix
to 4,096 units when the whole container fits a 14 MB growth budget and a bounded
additional CPU allowance. It retains the original result before attempting the
extension and records any fallback in partition metadata. The hard 16 MB worker
result ceiling, 17 MB transport ceiling, 20 MB checkpoint read ceiling and parser
CPU/memory limits remain unchanged. See [large-module growth](LARGE_MODULES.md#optional-growth-retains-the-original-bounded-result)
for the exact policy and the distinction between an effective per-result limit
and a guaranteed capacity for arbitrary code.

Large modules may retain every definition while exceeding the API analyzer's
separate source-size limit. Such reports remain `COMPLETED_WITH_GAPS`: searchable
definitions do not imply complete API-call analysis. Check both the definition
coverage and the recorded gaps before using a result.

The configured API analyzer computes module import bindings once per inspected
source, after resolving declared relative paths. Each function receives its own
copy before local imports, arguments and rebinding are checked. No import context
is retained across source scans. This removes repeated module-wide setup for
files with many functions; it does not guarantee capacity for arbitrary code.
The API worker's 12-second CPU limit, 768 MiB address-space limit, 25-second outer
timeout and 8,000,000-byte combined output limit remain unchanged. Limit failures
still produce explicit gaps instead of complete API coverage.

Literal symbol lookup traverses the saved definition metadata. Ranked search
also constructs an in-memory index and has its own record bound. A successful
scan or symbol lookup does not establish that every downstream operation fits
the same memory budget, or that the code can run correctly.
