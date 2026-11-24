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

Read the leads in `quest.json`. Idea mode searches the index. Scout mode samples up to 40 definitions across eligible modules. A builder can search the same corpus for additional definitions and inspect the original modules, imports and dependencies. Choose a user, a purpose, and at least one observable acceptance check. Copy `recipe.template.json` to `recipe.json`, fill those fields, and put the selected definition IDs in `components`. Each selected definition brings its **whole module** into the product. Use the optional `support_modules` list for indexed dependencies and package initializers. This builder supports ordinary Python files from a directory.

Write `main.py` to connect the selected code. Keep a separate acceptance check with expected results, including what should happen on bad input. The example below uses the files-first quest.

```sh
python3 forge.py --home "$WORK/home" quest-build --quest "$WORK/scout/quest.json" \
  --recipe "$WORK/recipe.json" --entry "$WORK/main.py" --source-root "$SOURCE" \
  --check-file "$WORK/check.py" --out "$WORK/product"
```

The new output directory contains `product.pyz` and `receipt.json`. The receipt lists the exact module, entry-point and archive hashes. If supplied, `--check-file` records the acceptance file's hash. Run a reviewed product separately using `python3 "$WORK/product/product.pyz"` with its expected arguments. Execute the acceptance checks against those same archive bytes and keep the results. A build receipt alone establishes what was packaged.

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
identity. Direct assembly currently uses ordinary Python files. A reviewed
extraction into a separate source folder can supply those files. Retain a mapping
from each original archive member to the extracted bytes, include its license and
index the extracted pool before building. Documents and non-Python components need
their own review. Archive discovery alone does not assemble a complete application.

## Learn from the result

Keep the candidate and the reason for its next decision. A failed experiment can
expose a missing input, an incompatible mechanism or an existing product that
already solves the job. A successful experiment establishes behavior for the
observed task. Adoption needs a practitioner using the result in their own work.
Feed those observations into a revised bundle and use `opportunity-compare` to
show what changed. Investigation priorities should follow the unresolved question
that could most affect the decision to build.
