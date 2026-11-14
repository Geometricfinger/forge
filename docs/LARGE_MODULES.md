# Large Python source: structure, inspection and lossless blocks

FORGE preserves one module identity and its original scopes. It does not turn
function fragments into standalone programs or run the inspected source.

## Separate budgets and explicit omissions

- Container read: at most 8,000,000 original bytes. ZIP member admission remains
  at most 2,000,000 bytes, with the existing expansion/depth/entry controls.
- Structural input: at most 2,000,000 UTF-8 bytes per Python segment. Ordinary
  modules up to 250,000 bytes retain the whole-module fast path when their AST
  fits 60,000 nodes. Larger modules go directly to complete top-level units;
  a smaller module whose AST exceeds that cap can use the same unit path.
- Structural units: at most 2,048 complete top-level statement units, each with
  at most 60,000 AST nodes. A compact shared declaration envelope also has a
  60,000-node cap. A lowered `max_nodes` applies to both caps. No function, class
  or compound statement is split internally to evade a limit. The fixed worker's
  CPU, memory, time and output limits are unchanged.
- Hound API analysis: at most 250,000 source bytes, unchanged. A larger module can
  have searchable definitions and dependency leads while still reporting
  `SOURCE_SIZE_LIMIT` / `SOURCE_LIMIT` and `COMPLETED_WITH_GAPS`.
- Complete-definition inspection: at most 250,000 UTF-8 bytes. A larger definition
  remains in the inventory with `DEFINITION_SIZE_LIMIT`; no truncated body is
  presented as a complete function. A caller can lower the inspection budget.

The coverage report distinguishes discovered definitions, available and omitted
inspection windows, API-analyzed definitions, and large modules. If an entire
module is unreadable or exceeds a structural limit, its unknown definition count
is not reported as zero known omissions. Nested definition windows overlap, so
their sizes are not summed to claim a percentage of source-byte coverage.

## Complete units retain their shared module

The token scanner locates complete top-level statements in an original source
view. Decorators stay with their declaration; `elif`, `else`, `except` and
`finally` stay with their compound statement. This scan selects original slices;
it does not regenerate or untokenize source.

Two bounded passes avoid keeping one large module AST in memory. The first
parses each unit and builds a compact envelope retaining declaration headers,
module imports and module effects while discarding declaration bodies. The
second parses each admitted unit against that prepared shared module context.
Nested functions and classes remain inside their complete original unit. Source,
segment and definition identities use the same original bytes and coordinates.
If a file consists of one indivisible unit, that unit still requires parsing as
a whole and can exceed its node or worker resource allowance. Partitioning does
not guarantee that every large function or class can be analyzed.

Each partitioned segment records `structural_partition`: its policy, limits,
unit counts, exact original-byte offsets/hashes and admitted/omitted states.
Corpus coverage aggregates those counts. Search rows distinguish `COMPLETE`
from `PARTIAL` structural partitions; this label concerns partition coverage,
not runtime resolution or semantic completeness. Existing bounded binding and
call records can still be truncated even when every unit is admitted.

An indivisible over-budget unit produces `AST_UNIT_LIMIT`; a shared envelope
that cannot fit produces `MODULE_CONTEXT_LIMIT`. A tail beyond the unit-count
budget produces `STRUCTURAL_UNIT_COUNT_LIMIT` with its exact range and count.
Those cases preserve usable neighboring definitions, but mark module context
incomplete and withhold imported API leads and cross-definition call candidates.
They never turn unknown omitted definitions into a known count of zero.
An imported candidate also depends on the target module's coverage. If any
plausible target module is structurally incomplete, dependency context reports
`TARGET_MODULE_CONTEXT_INCOMPLETE` and withholds candidates for that import,
including when the omitted code could contain the requested name.

Syntax or lexical errors encountered while scanning/parsing still reject the
whole structural module. An unparsed resource-limited unit or count-limited tail
explicitly records incomplete syntax validation. A module's bytes can be
preserved even when its structure or complete syntax cannot be established.

## Inspect an exact definition

Use `corpus-search` to obtain a definition ID, then:

```sh
python3 forge.py --home /path/to/existing-work corpus-inspect \
  --run-id example --definition definition_<64 hex characters> \
  --source-root /path/to/original-sources
```

This command explicitly returns source text to its caller. Source bodies remain
absent from saved corpus reports and search exports. The original container is
reacquired, size/hash checked, and parsed by the same bounded worker. The command
checks the module, definition identity and complete source window before returning
text. It rejects a changed original, a missing original, an altered window, or a
window larger than the permitted budget. It does not reconstruct code from names,
docstrings, search results or hashes.

Window offsets identify UTF-8 segment bytes, while container and ZIP-member hashes
bind the original bytes and encoding. Decorators, indentation and physical newline
sequences remain in the returned window. A nested function's window is still part
of its original enclosing scope, not a new executable component.

Decorator windows start at the complete logical `@` statement, including
parenthesized or backslash-continued expressions whose first AST node appears on
a later physical line. Token boundaries are used only to locate that statement;
the returned window still contains the original source bytes.

Dependency context includes bounded syntax leads for decorators, defaults,
annotations, class bases/metaclasses and referenced bindings. Imports, globals,
closure candidates and ambiguous/rebound names remain distinct. These are static
leads: runtime dispatch and complete dependency closure are not established.
Assignment expressions in defaults, decorators and class headers contribute
binding leads to their defining scope without treating nested bodies as part of
that scope. Postponed annotations do not contribute binding effects; generic
declaration and comprehension effects retain explicit uncertainty.

## Reconstruct the original bytes

`forge_core.source_blocks.deconstruct(data)` divides up to 8 MB of original bytes
into blocks of at most 250 kB. Its manifest records contiguous offsets, ordering,
sizes and per-block/full-source SHA-256 hashes. Optional preferred boundaries can
align blocks with definitions. A definition larger than a block may occupy several
storage blocks without changing its single analysis identity.

`reconstruct(manifest, blocks)` verifies every block and their exact membership,
ordering, coverage and full-source hash. Corpus intake additionally requires the
reconstructed bytes to equal the original bytes, then retains only the manifest
and verification result. It does not retain the block bodies.

```python
from forge_core.source_blocks import deconstruct, reconstruct

packet = deconstruct(original_bytes)
restored = reconstruct(packet['manifest'], packet['blocks'])
assert restored == original_bytes
```

This preserves comments, whitespace, BOMs, encodings and line endings because
reconstruction concatenates original bytes rather than generating Python text.
To restore a file after losing the original, a caller must separately retain all
block bodies. Hashes alone cannot recreate them. Block integrity is separate from
correct interpretation, runtime behavior and authenticity of the source provider.
