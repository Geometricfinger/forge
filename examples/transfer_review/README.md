# Declared transfer review example

All inputs here are synthetic. The labels are operator declarations, not inferred
or verified descriptions of executable components. A digest binds supplied text,
not the truth of that text.

From the repository, with a fresh workspace outside the checkout:

```sh
python3 forge.py --home /absolute/external/forge-review opportunity-review \
  --bundle examples/transfer_review/bundle.json \
  --registry examples/transfer_review/registry.json
```

`record` has a declared supporting role. `matching` lacks distinguishing
information. Neither is a verified or authorized implementation. The unchanged
Opportunity Lab result is retained in `original_report`; review happens before
the original display cutoff. The return code reports whether the comparison
completed, not whether any candidate is fit to ship. `--out` writes a new JSON
report and refuses to overwrite an existing one.

To author a registry, supply `schema`, `bundle_sha256`, `annotation_basis`,
`mechanisms` and `workflows` as in registry.json. Hash with Forge's canonical JSON
(`forge_core.common.canonical`), not formatted JSON. Mechanism/source/workflow
bindings detect stale declarations. Inputs/outputs specify meaning, units, frame
and clock; use `unknown` where undetermined. Required information is available
only before the workflow step actually under review. No domain-name blacklist,
automatic unit conversion, inference, execution or authorization is provided.

The API example generator is `forge_core.transfer_example.example()`. It only
returns this hand-written synthetic fixture. It is not a contract-inference tool.
