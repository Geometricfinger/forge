# Markdown source and its analysis view

FORGE preserves the original UTF-8 segment bytes when analyzing a closed Python
code fence. A fence may open with zero to three ASCII spaces of indentation. For
an indented opening fence, the analysis view removes up to that indentation from
each body line, following the indentation rule in
[CommonMark 0.31.2, section 4.5](https://spec.commonmark.org/0.31.2/#fenced-code-blocks).
This is a per-line prefix operation, not a common-indent guess or general Python
source repair. A line with less indentation loses only the indentation present;
an unindented line remains unchanged. Leading tabs use four-column block stops,
with a partially removed tab leaving spaces. Tabs beyond the removed prefix and
all physical newline sequences remain unchanged.

The existing explicit-fence admission rules still apply. FORGE does not claim to
implement all Markdown parsing, lists, block quotes, or unclosed-fence semantics.
Plain Python files are not dedented: unexpected indentation remains a syntax gap.

## Two identities with an explicit relationship

- `source_hex` is transient transport of the original UTF-8 fence body. The
  segment's `source_sha256`, logical segment ID and definition IDs retain their
  original-source basis. Container hashes still bind original container bytes
  and encoding.
- An indented fence adds `analysis_view`: its fixed policy, opening indentation,
  original and analysis sizes/hashes, identity line mapping, and analysis-column
  basis. The view is deterministic and can be recomputed from the original body.
- The structural parser and Hound inspect the same analysis bytes. Hound's own
  `source_sha256` and finding hashes describe those bytes; they are not rewritten
  to imply that Hound parsed the original indented body. The surrounding segment
  and search records carry the relationship to the original source.
- Source windows remain slices of complete original lines, including original
  indentation, decorators and CR/LF/CRLF bytes. AST columns belong to the analysis
  view; they must not be interpreted as original-source byte columns.

Large fences use the same bounded complete-unit structural path as other large
modules. Public partition units retain original-source byte ranges, sizes and
hashes. Each transformed unit also carries `analysis_window`, whose explicitly
labeled `UTF8_ANALYSIS_VIEW` offsets/hash and module hash describe the bytes the
parser actually received. The partition binds the original source hash and the
canonical `analysis_view` hash. Omitted units retain both ranges as well. The
internal parse plan continues to use analysis slices; mapping public metadata
back to raw lines does not change its node, unit, envelope or worker limits.

Dependency context includes the caller's view when present. A candidate's source
identity carries its own target view, which can differ from the caller's. These
metadata fields do not make a candidate a proven runtime dependency.

The normalized body is never stored in corpus reports, exports or checkpoints.
Only its binding metadata is retained. The parser does not send a second body
through its output pipe: the corpus stage recomputes the view before Hound runs.

## Budgets and verification

Both the original segment and the analysis view must satisfy the existing
structural byte allowance. Both must satisfy Hound's unchanged source allowance
before an analyzer attempt is admitted. Removing indentation therefore cannot
admit an originally oversized source, and expansion of a partially consumed tab
cannot bypass the analysis-input limit. Inspection limits still apply to the
original complete source window.

The view's policy, sizes and hashes are checked against a fresh deterministic
reconstruction before analysis. Segment checkpoints and recovery journals include
the view binding. Reacquisition reparses the unchanged original container and
checks the regenerated view metadata as well as definition identity and original
window hashes. A changed or removed view binding is rejected; successful replay
uses completed checkpoints without another parser or analyzer run.

Research Runner records carry the same optional view metadata for both functions
and module scope. Initial candidates, compact packets, follow-up candidates and
source inspection preserve it beside original source hashes and excerpts. Runner
checks that its second parse agrees with the corpus view before retaining source,
and checks the view again when attaching corpus API observations. Altered or
missing metadata cannot inherit those observations. Packet size limits may omit
a complete evidence item; they do not remove its view binding to make it fit.

These checks preserve evidence identity. They do not establish runtime behavior,
source-provider authenticity, or suitability for reuse, and they do not execute
the inspected source.
