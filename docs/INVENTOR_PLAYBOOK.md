# An inventor's workflow in FORGE

## Start with a task, not an app category

Record one concrete episode: the actor, the artifact, what the actor wanted to accomplish, what information was available, the manual workaround, the consequence, and the source of that observation. Keep an expert's firsthand experience valuable without pretending it represents an entire market.

## Map the handoff

Use Opportunity Lab's guided form or a reviewed structured brief. List what each step REQUIRES and PRODUCES. A step with unmet mandatory inputs does not supply verified outputs to later steps. Separate independent or conditional outputs into distinct steps. Descriptions are a model of the workflow, not executed truth.

Example: a received part needs an identity for the next operation. Attribute comparison may narrow candidates, but distinguishing information may be absent. The useful intervention might combine shape comparison with acquisition/provenance records and explicit requests for another observation. This is an application hypothesis, not a new market finding or validated workflow.

## Ask for alternatives, not agreement

Read Next evidence to collect. Investigate whether an existing product, a setup change, a manual workaround, or a nonsoftware intervention already solves the task adequately. Seek observations that would disprove the proposed gap. Several excerpts from one article are still one source origin in this bookkeeping; independence remains unverified.

## Search at two levels

Search `hash`, `matching`, or `receipt` to find candidate mechanisms. Use `symbol:file_hash` or a qualified name when deliberately selecting an exact symbol. A full lexical match still does not establish units, coordinate frames, guarantees, side effects, or dependency compatibility.

Inspect helper context and source versions. Your own private code is a source of implementation leads and regression material, not proof of customer need. Use licensed components through adapters when appropriate instead of rewriting everything for novelty in appearance.

## Define the smallest useful experiment

Specify the existing workflow, the same permitted inputs, one bounded intervention, and expected correct/incorrect/ambiguous outcomes. Record operator actions and effort, failures, integration burden, and resource use. Test the assumption most likely to invalidate the idea before building a large interface.

For an identity problem, include two candidates indistinguishable in the available observation. The system must request more information or abstain; a confident forced identity is not an improvement. Also include an existing solution that already works. If it solves the job adequately, reuse it or choose a genuinely different requirement.

## Keep six decisions separate

1. A problem was observed.
2. It occurs under a defined scope beyond one artifact.
3. Existing alternatives have a demonstrated limitation for that scope.
4. The proposed mechanism has the needed information and a feasible integration.
5. A prototype improves the actual task without required regressions.
6. A customer has demonstrated willingness and ability to adopt it.

An unusual idea, low keyword similarity, or a high model score proves none of these by itself. Patent novelty and rights review are separate from this workflow and remain unestablished by FORGE.

## What the inventor should receive

A concept dossier should retain the problem and episode, sources and counterevidence, competing options, a purpose/mechanism explanation, exact code leads, unknown prerequisites, a bounded experiment, actual results when available, reasons not to build, and a next decision. The current build supplies the reviewed-record, code-lead and plan parts; it does not automatically execute interviews or generated product experiments.

The user can use judgment to redirect the investigation without having to write technical system prompts. Store the reason for accepting or rejecting a proposal so subsequent investigations can reuse the decision context. Preference must never overwrite contradictory evidence.

## Run a creation quest

An agent or a person can drive a quest. Give the builder a job and permission to finish it. For an idea-first quest, try “Build a tool that tells me which files changed after I handed off a folder.” For a files-first quest, try “Look through these modules. Find two that solve a concrete job together. Pick a user, show the input and useful output, and build the smallest version I can try.” Either quest can end with a recorded reason to stop if the pieces do not support a useful product.

Start in the Forge checkout, with a physical work directory outside the checkout. Set `SOURCE` to the folder of Python modules you have permission to reuse. Pick one starting mode for each run.

```sh
python3 forge.py --home "$WORK/home" corpus-run --source-root "$SOURCE" --run-id parts
python3 forge.py --home "$WORK/home" quest-start --run-id parts --out "$WORK/idea" \
  --idea "find changed files by comparing content fingerprints"
python3 forge.py --home "$WORK/home" quest-start --run-id parts --out "$WORK/scout" --scout
```

Read the leads in `quest.json`. Idea mode searches the index. Scout mode samples up to 40 definitions across eligible modules. A builder can search the same corpus for additional definitions and inspect the original modules, imports and dependencies. Choose a user, a purpose, and at least one observable acceptance check. Copy `recipe.template.json` to `recipe.json`, fill those fields, and put the selected definition IDs in `components`. Each selected definition brings its **whole module** into the product. Plain Python files keep their original import paths by default; `support_modules` selects indexed dependencies and package initializers at those paths. For archived modules, explicit path mappings, resources and notices, use the recipe fields below.

Write `main.py` to connect the selected code. Keep a separate acceptance check with expected results, including what should happen on bad input. The example below uses the files-first quest.

```sh
python3 forge.py --home "$WORK/home" quest-build --quest "$WORK/scout/quest.json" \
  --recipe "$WORK/recipe.json" --entry "$WORK/main.py" --source-root "$SOURCE" \
  --check-file "$WORK/check.py" --out "$WORK/product"
```

The new output directory contains `product.pyz`, `receipt.json` and `preflight.json`.
The receipt lists the exact module, entry-point, archive and preflight hashes. If
supplied, `--check-file` records the acceptance file's hash. Review the preflight
issues, then run an authorized, reviewed product separately using
`python3 "$WORK/product/product.pyz"` with its expected arguments. Execute the
acceptance checks against those same archive bytes and keep the results. A build
receipt establishes what was packaged; runtime validation remains `NOT_RUN`.

### Review packaging before execution

Every build checks the exact selected Python bytes in fixed compiler workers.
The workers compile source without importing or executing it, then collect
bounded import and literal package-resource syntax. They use the interpreter
that runs Forge. Compilation can reveal an encoding problem that normalized
corpus analysis did not expose, including preserved UTF-16 source bytes.

`preflight.json` records each file's raw digest and compilation result, together
with issues, source lines and suggested next steps. The CLI reports its status
and issue count. `NO_ISSUES_OBSERVED` means these checks found no issues;
`REVIEW_REQUIRED` means there are advisory findings; `INCOMPLETE` means a worker,
capture or report limit prevented the complete bounded check. Packaging retains
the original bytes and all warnings; a warning does not silently change imports,
transcode modules or create package initializers.

The receipt binds the exact `preflight.json` bytes by hash alongside the product
hash. Assessment validates the optional summary and preserves the original
receipt; it does not load that sidecar or rerun its checks. Compare the sidecar's
hash with the receipt before using a separately supplied preflight report.

| Preflight lead | Review action |
|---|---|
| Missing direct or relative import under a selected package | Select the needed module or review the conditional import. Imported attributes and dynamic imports remain unresolved. |
| External import | Verify the dependency in the target environment. Forge does not import or install it. |
| Literal package resource missing from the selected files | Select the exact resource at the expected destination. Aliased `importlib.resources` and `pkgutil` calls are recognized only when their module-level imports are unambiguous. |
| Namespace package resource in a ZIP | Review the loader's ZIP support; select an original initializer explicitly when appropriate. Namespace imports alone do not trigger this warning. |
| Eager import cycle | Review initialization order. Delayed function-body imports do not create eager edges; conditional imports may still produce a review lead. |
| Top-level standard-library name | Review potential shadowing; nested modules and names built into the interpreter are distinguished. |
| Raw Python compilation rejection | Review syntax and encoding for the intended Python version. Raw bytes remain unchanged. |
| Non-NFKC module name or normalized-name collision | Review the destination mapping against Python's identifier normalization. Exact-string dynamic import behavior can differ. |

The checks do not establish dependency closure, external package availability,
successful initialization, compatibility with another interpreter or operating
system, or runtime correctness. Dynamic resource paths and ambiguous/rebound
aliases need manual review. Package resource anchors that name ordinary modules
remain unresolved because interpreter versions differ. Resource checks do not
read any files outside the selected bytes. Limits are 60,000 AST nodes per
module, 256 import/resource references per kind per module, 512 retained issues
and a 45-second worker wall budget for the product. Linux workers also have
fixed address-space, CPU and file-descriptor limits. These are resource controls,
not a hostile-code sandbox.

Some unresolved expressions can appear in a `NO_ISSUES_OBSERVED` report: a
`from . import name` member could be an attribute or submodule; even a literal
`import_module("pkg.name")` call remains dynamic; and resource objects stored in
variables or resource APIs imported inside functions are not followed. Review
these cases separately rather than treating the absence of a warning as proof
that their dependencies or resource files are present.

### Assemble directly from an indexed ZIP

Keep `--source-root` pointed at the original corpus files. Add `module_files` to
map each selected archived module's `source_id` to its intended Python import
path. A mapping also explicitly selects an indexed support module, including an
initializer without any functions. Leads carry their source ID and member chain;
the retained `corpus.json` lists every indexed module under `sources[].segments`.
Use a complete `.py` module, not a notebook cell, stub or document code fence.

This is a recipe fragment; replace the placeholders with actual retained IDs and
the hashes and sizes of the original member bytes:

```json
{
  "module_files": [
    {"source_id": "<indexed module source_id>", "path": "package/reader.py"},
    {"source_id": "<indexed initializer source_id>", "path": "package/__init__.py"}
  ],
  "assets": [
    {
      "file_id": "<corpus container file_id>",
      "member_chain": [{"path": "project-1.0/package/data.json", "sha256": "<member SHA-256>"}],
      "path": "package/data.json", "bytes": 24, "sha256": "<member SHA-256>", "role": "resource"
    },
    {
      "file_id": "<corpus container file_id>",
      "member_chain": [{"path": "project-1.0/LICENSE", "sha256": "<member SHA-256>"}],
      "path": "licenses/package/LICENSE", "bytes": 1000, "sha256": "<member SHA-256>", "role": "notice"
    }
  ]
}
```

For a nested ZIP, include each member from the outer archive to the final file,
with the raw SHA-256 at each step. For an ordinary resource or notice file,
include that file in the corpus manifest and use an empty `member_chain`.
Automatic folder intake selects supported code/text formats; an extensionless
license or JSON resource needs an explicit manifest entry. Resource and notice
contents are opaque bytes and need not be analyzed as code.

The existing `quest-build` command verifies the original container and selected
members, then writes their unchanged bytes into the final archive. It creates no
extraction directory and requires no second corpus scan. Raw module encodings,
line endings, resources and selected notices are preserved. The optional
`source_files` receipt records each packaged path, raw digest, original container
and member chain; module origins separately label the normalized analysis hash.

Select the original package initializers explicitly and map them to their
corresponding destination packages. An unrelated initializer cannot substitute
for the one belonging to a selected module. Output paths must be unambiguous:
file/directory overlaps, case-folded duplicate paths and module/package collisions
are refused. Python and native importable files cannot be supplied as opaque
assets; Python modules must come through the indexed module selection.

The product accepts at most 32 whole modules and 64 assets. Modules, assets and
the entry point share an 8,000,000-byte payload budget. ZIP member reads retain the
intake size, nesting, entry-count, compression-ratio and path restrictions.

The builder still reviews reuse rights, chooses the needed notices and resources,
writes the adapter and supplies dependencies. Retaining a notice is not license
clearance. Mapping a package does not establish dependency closure or repair its
imports. Code requiring native extensions or filesystem-only resources needs a
separate execution and packaging approach. Test the exact completed archive.

To connect those results to the intended outcomes, add an `evaluation_plan` to
the recipe before building. Give each case a fixed expected JSON observation,
an acceptance-check index and any promised needs it exercises. After the external
run, use `quest-assess` with the original recipe, receipt, archive, checker and
recorded artifacts. It retains passing, failing, incomplete and unaddressed work
in a separate assessment. See [Assess a creation quest](QUEST_ASSESSMENT.md).

## Start from a complete observed workflow

Use the observation to choose a useful outcome before selecting a small set of
functions. Record who does the work, what decision they need to make, the inputs
they receive, where they lose time or information, and what their current tools
already accomplish. Include the source and the date of each observation.
An agent supplies this interpretation through the existing Opportunity Lab bundle.

Search for mechanisms at several levels. A module may provide interval arithmetic.
A package may provide a scheduling service. A complete workflow might connect a
failure diary, service calendar, work history and review queue so a coordinator
can decide what to inspect. Check each conversion between representations and
preserve the original evidence behind the resulting recommendation.

A saved opportunity can now start a creation quest for an entire workflow.

```sh
python3 forge.py --home "$WORK/home" opportunity-run --bundle "$WORK/observations.json" \
  --corpus-run parts
python3 forge.py --home "$WORK/home" quest-start --run-id parts \
  --opportunity-run OPPORTUNITY_RUN_ID --workflow WORKFLOW_ID --out "$WORK/quest"
```

The quest retains the workflow's purpose, operator, needs, sources, competing
options, conflicting evidence, missing prerequisites and matching code leads.
The recipe template carries the opportunity identity and intended purpose.
For each need the prototype will address, add a response with its useful outcome
and the selected definition IDs. For example, using identifiers from that quest,

```json
{
  "need": "operation_intervals",
  "outcome": "Show the remaining scheduled periods after applying confirmed interruptions.",
  "components": ["definition_ID_FROM_THIS_NEED"]
}
```

Place those objects in the recipe's `need_responses` list, alongside the ordinary
recipe fields and the supplied `opportunity` reference. Each response must use
source leads for that need and components selected in the recipe. Build with the
same `quest-build` command. Its receipt retains the original purpose and sources,
the proposed outcomes and every remaining need. The separate acceptance run must
show which proposed outcomes the packaged product actually achieves.

For arbitrary repositories and ZIPs, begin with `corpus-run`, inspect promising
source definitions and their dependency context, and preserve the archive's exact
identity. The explicit module and asset mappings above carry selected Python
modules, resources and notices directly into the product. Documents and
non-Python components need their own review. Archive discovery alone does not
establish that all application dependencies have been selected.

## Learn from the result

Keep the candidate and the reason for its next decision. Quest assessment carries
the original purpose evidence into a per-need outcome record and proposes the
next evaluation decision. A failed experiment can
expose a missing input, an incompatible mechanism or an existing product that
already solves the job. A successful experiment establishes behavior for the
observed task. Adoption needs a practitioner using the result in their own work.
Feed those observations into a revised bundle and use `opportunity-compare` to
show what changed. Investigation priorities should follow the unresolved question
that could most affect the decision to build.
