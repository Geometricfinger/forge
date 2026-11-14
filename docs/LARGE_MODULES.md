# Large Python source: structure, inspection and lossless blocks

FORGE preserves one module identity and its original scopes. It does not turn
function fragments into standalone programs or run the inspected source.

## Separate budgets and explicit omissions

- Container read: at most 8,000,000 original bytes. ZIP member admission remains
  at most 2,000,000 bytes, with the existing expansion/depth/entry controls.
- Structural parse: at most 2,000,000 UTF-8 bytes per Python segment and 60,000 AST
  nodes. The fixed parser worker's CPU, memory, time and output limits are unchanged.
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

Dependency context includes bounded syntax leads for decorators, defaults,
annotations, class bases/metaclasses and referenced bindings. Imports, globals,
closure candidates and ambiguous/rebound names remain distinct. These are static
leads: runtime dispatch and complete dependency closure are not established.

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
