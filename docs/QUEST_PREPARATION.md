# Prepare reviewed components for a creation quest

`quest-prepare` turns an existing reviewed recipe into a build-compatible recipe
with the original indexed package initializers and a source review report. It
reads the selected original bytes without importing or executing them. It does
not generate an adapter or build a product.

Start with a saved corpus, a `quest.json` from `quest-start`, and a completed
schema-1 recipe. Choose the audience, purpose, acceptance checks and components.
For archived or renamed modules, supply the intended import paths in
`module_files`. Explicit support modules, assets and evaluation plans use the
same fields as `quest-build`.

```sh
python3 forge.py --home "$WORK/home" quest-prepare \
  --quest "$WORK/quest/quest.json" --recipe "$WORK/recipe.json" \
  --source-root "$SOURCE" --out "$WORK/preparation"
```

The source root, workspace and new output directory must remain separate. Keep
the original recipe and every preparation attempt; a changed selection is a new
review decision.

## What the command does

1. Recomputes the quest from its bound corpus and, when applicable, the original
   Opportunity Lab workflow. It validates the recipe and evaluation plan.
2. Resolves the explicitly selected whole modules using the same rules as the
   builder. Plain files retain their existing default import paths. Archived
   modules require explicit destinations; the command does not guess which
   archive prefix to remove.
   Product and member paths must be valid UTF-8 text; malformed surrogate
   characters are refused before either preparation or archive serialization.
3. Adds each original indexed `__init__.py` required by those package paths.
   A nested package retains its required parent initializers. An unrelated
   initializer, conflicting mapping or flattened package is refused. An existing
   initializer that was not indexed must be indexed before preparation can use it.
4. Reacquires the selected modules, resources and notices through the builder's
   existing bounded file/ZIP reader and checks their raw byte identities.
5. Runs the existing advisory compiler/import/resource preflight on the selected
   modules. No entry point is supplied at this stage. Existing lexical call
   context can expose additional indexed support leads for explicit review.

Only `module_files` changes in the prepared recipe. All supplied audience,
purpose, acceptance, evaluation expectations, need responses, support selections
and asset references remain equal as JSON values. No source bytes are rewritten.

## Review the output

| File | Purpose |
| --- | --- |
| `recipe.prepared.json` | Ordinary schema-1 recipe accepted by `quest-build`. Its module mappings include the required original indexed initializers. |
| `preparation.json` | Input and output recipe hashes, quest/corpus bindings, original source mappings, each added initializer, support review leads and unresolved responsibilities. |
| `source-preflight.json` | Exact module byte hashes and advisory compiler/import/resource findings. This review covers selected modules only. |

The command reports `RECIPE_PREPARED_REVIEW_REQUIRED` even if source preflight
reports `NO_ISSUES_OBSERVED`. The preparation always records
`entry_point_reviewed: false`, `product_built: false`,
`dependency_closure_complete: false` and `runtime_validation: NOT_RUN`.

Review support leads against complete source modules. They come from existing
unambiguous declared-import call context for the selected definitions; module
initialization, imported attributes and dynamic dependencies may require other
files. The report exposes incomplete context and any omitted support candidates.
It never selects a support lead automatically.

Select needed dependencies and package data explicitly. Review source terms and
include the required original notices. A selected notice does not establish
reuse permission, and a missing notice entry does not establish a license
violation. Repeat preparation in a new output directory after revising selections.

Write and review the adapter, then build:

```sh
python3 forge.py --home "$WORK/home" quest-build \
  --quest "$WORK/quest/quest.json" \
  --recipe "$WORK/preparation/recipe.prepared.json" \
  --entry "$WORK/main.py" --source-root "$SOURCE" \
  --check-file "$WORK/check.py" --out "$WORK/product"
```

The builder rechecks all identities and runs preflight again with the actual
entry point. Test those exact archive bytes externally when execution is
authorized. Use [quest assessment](QUEST_ASSESSMENT.md) to compare the recorded
observations with the fixed expectations. Preparation does not approve execution
or release and does not authenticate external runtime evidence.

## Fix a refused field

Existing refusal codes are preserved. Text and evaluation-plan errors can add a
`detail` object containing a static field location and guidance, without echoing
the supplied value. For example, an invalid evaluation case ID identifies
`evaluation_plan.cases[2].id`. Give the evaluation case a stable ID while keeping
the actual record identifier in its fixture unchanged.

The idea limit is 200 characters. Evaluation case IDs use 1 to 80 ASCII letters,
digits, underscores or hyphens and start with a letter or digit. Each declared
input/observation artifact must fit the 1,000,000-byte assessment limit; all
unique retained artifacts share an 8,000,000-byte budget. Keep larger boundary
experiments separately and state their exclusion rather than changing the data
to imply they passed assessment.
