# Build options from reviewed evidence

Opportunity Lab can compose several routes to one declared workflow output. A
route may use an existing product, a source component, manual work, a new adapter,
or a combination. Each route retains ordered steps, prerequisites, citations,
operating conditions and remaining implementation work.

This makes a reviewed mechanism inventory actionable when the builder has no
single solution in mind. The operator or supervising agent still supplies the
problem interpretation, product research, mechanism contracts and implementation.
Forge does not infer missing capabilities from listings, invent an undeclared
adapter, call a model, follow evidence URLs, or execute a route.

## Begin with the complete job

Describe the actor, available information, required decision and operating
conditions. Review product listings, documentation, workflow observations and
permitted source components. An inventory supplies possible alternatives; its
omissions do not establish a market gap.

Keep these distinctions in the annotations:

- A documented capability is a declaration until relevant behavior is tested.
- A documented limitation has a particular product, version and task scope.
- An unmentioned capability is unknown.
- An existing configuration or manual process can satisfy the job without new
  software. Include it in the mechanism catalog.
- Synthetic examples and source inspection can test reasoning, but cannot supply
  firsthand customer pain or establish demand.

The same output name must mean the same declared scope on every route. A
reviewable summary is a broader output than an independently verified summary;
give them different field names unless their contracts establish the same
meaning. A shared broad output does not give every route the same cost,
completeness or accuracy guarantee. Retain those differences in `limits` and in
the acceptance plan.

## Optional input fields

The existing Opportunity Lab schema remains in use. All legacy fields are still
required. Three optional additions describe roles and operating conditions:

| Location | Field | Meaning |
| --- | --- | --- |
| Workflow | `required_conditions` | Up to 16 unique condition IDs that every route step must address |
| Mechanism | `role` | `existing_option`, `source_component`, `new_work`, `manual_step` or `unclassified`; omitted means `unclassified` |
| Mechanism | `conditions` | Up to 16 unique declarations, each containing `condition`, `status` and `evidence` |

Condition IDs use the ordinary ID syntax: an alphanumeric first character,
followed by alphanumerics, `_` or `-`, at most 80 characters. Status is exactly
`supported`, `incompatible` or `unknown`. Each explicit declaration has one to
eight ordinary evidence citations. An explicit unknown declaration still cites
the scope of the review. If a required condition has no declaration, synthesis
retains it as unknown without inventing a citation.

These fragments show only the added fields; retain the legacy required fields.
Suppose an attributed or synthetic source named `local_step_review` retains
exactly `This reviewed step uses local files.`:

```json
{
  "required_conditions": ["local_processing"]
}
```

```json
{
  "role": "source_component",
  "conditions": [
    {
      "condition": "local_processing",
      "status": "supported",
      "evidence": [
        {
          "source": "local_step_review",
          "start": 0,
          "end": 36,
          "quote": "This reviewed step uses local files."
        }
      ]
    }
  ]
}
```

Exact citation checks establish correspondence with retained text. They do not
establish entailment, successful behavior or permission to reuse. A role is also
an annotation: `existing_option` does not verify product availability, and
`source_component` does not approve a license.

## How routes are composed

For each workflow need, the target is its exact `field`. Initial availability is
the information declared reachable before that need's step, using the existing
ordered workflow trace. A later workflow producer cannot repair an earlier
consumer retroactively.

Synthesis follows possible producers backward from the target, including
supporting mechanisms from other pattern families. It searches forward with
every declared input available before a mechanism adds its outputs. Each step
records whether an input comes from the workflow or an earlier mechanism.
There is no hidden type conversion, synonym expansion or inferred adapter.

This model accumulates information. It does not model destructive effects,
consumed resources, probabilistic outcomes, conditional answers, time windows
or competing costs. Such behavior needs a separately reviewed mechanism and
acceptance experiment. Matching field names does not prove behavior.

An `incompatible` declaration for any required condition excludes a mechanism.
Unknown conditions stay visible. Supported declarations produce
`DECLARED_SUPPORTED`; every option still has `implementation_tested: false`.

## Read the decision before selecting a route

| Status | Next action |
| --- | --- |
| `BUILD_OPTIONS_FOR_REVIEW` | Inspect routes and choose a bounded comparison |
| `EXISTING_OPTION_REVIEW_REQUIRED` | Check the cited existing solution; new-work synthesis is gated |
| `EVIDENCE_REVIEW_REQUIRED` | Resolve conflicting, contrary or stale evidence first |
| `NO_NEW_WORK_DECLARED_OUTPUT_AVAILABLE` | The workflow already supplies the required field |
| `NO_DECLARED_ROUTE` | Inspect missing inputs, exclusions and unseeded cycles; no route follows from this catalog |
| `NO_PLAN_FOUND_WITHIN_BOUNDS` | Review limits; no complete route was retained within them |

The existing evidence states determine the gates. Fresh target-scoped supported
observations produce existing-option review. Counterevidence, a qualifying
conflict, or stale-only evidence pauses build recommendations. Analogous product
features inform counterresearch without automatically settling the target job.
The engine does not fabricate firsthand evidence to reach a stronger state.

If synthesis finds a supported existing-only route, `next_decision` asks the
reviewer to verify it before new work. A configuration, manual process,
investigation or stop decision can be the useful outcome.

## Inspect the option and its evidence

The live UI and exported `Review.html` display the role classification, ordered
steps, input origins, conditions, new-work obligations, limitations, source
queries, next decision and search coverage. Classifications include
`REUSE_EXISTING_OPTION`, `REUSE_SOURCE_COMPONENT`, `COMBINE_DECLARED_MECHANISMS`,
`MANUAL_PROCESS`, `NEW_WORK_REQUIRED` and `UNCLASSIFIED_REVIEW_REQUIRED`.

Compact evidence locators retain source ID, character range and quote digest.
The full reviewed citations remain in the report's mechanism catalog; source
metadata remains in its register. Displaying a locator does not follow its
source or execute its contents.

The exact serialized saved record, including input, report and checksum, is
limited to 10,000,000 bytes. `OPPORTUNITY_RECORD_BOUNDS` refuses a larger record
before publication. Split an oversized reviewed collection into smaller records;
do not treat a refusal as a completed investigation.

Different mechanism sets are distinct choices in this search. Their practical
behavior and novelty still require review. An existing export followed by manual
reconciliation may have the same broad output as a new optimizer, with different
guarantees and operator work.

Unresolved diagnostics describe relevant catalog branches, including branches
unused by retained options. An absent provider, unreachable mechanism, unseeded
cycle or condition exclusion is not necessarily a defect in every option, and
the diagnostic is not a minimal recovery plan.

## Search limits and ordering

The search retains at most 4 steps per route, 4,096 mechanism-set states and 8
displayed options. Reports retain relevant/eligible counts, states expanded,
equivalent orders collapsed, complete sets found, redundant complete sets
omitted, options omitted, and transitions omitted by depth or state limits. Gates
that prevent searching leave reachability/completion unassessed; the UI does not
turn that into a false result.

Completed routes omit redundant supersets found during search. This inclusion
minimality concerns mechanism sets for a single declared output. It is not
minimum implementation effort, runtime, operator time, evidence-request cost or
market value. A bounded search does not establish that other solutions are absent.

Review ordering prefers supported declarations over unknown conditions, fewer
new-work mechanisms, a classified route, fewer steps and stable mechanism IDs.
The shortlist first retains a representative of each available role
classification, then fills remaining slots in that order. These are review
policies, not estimates of success. A short unbuilt route can appear before a
longer component combination without being proven better.

## Operator workflow

For a declaration-only local example, use the owned synthetic bundle. It shows
three mechanism routes and an existing-solution control without fetching source
or executing a product:

```sh
python3 forge.py --home /path/to/new-workspace opportunity-run \
  --bundle examples/invention_options_synthetic.json
```

The example's capability and condition statements are synthetic. They illustrate
the contract schema and must not be presented as observed product behavior.

1. Record the complete task and research existing products and configurations.
   Retain support, limitations and unknowns with source scope and dates.
2. Write reviewed workflows and mechanisms with exact field meanings, explicit
   conditions, visible manual steps and citations.
3. Run Opportunity Lab in a workspace outside the checkout and source corpus:

   ```sh
   python3 forge.py --home /path/to/workspace opportunity-run \
     --bundle /path/to/reviewed-bundle.json --corpus-run parts
   ```

4. Inspect the decision, routes, source leads and coverage in the report, UI or
   private export. `--corpus-run` is optional for declaration-only evaluation; a
   creation quest requires a saved source corpus.

   ```sh
   python3 forge.py --home /path/to/workspace opportunity-export OPPORTUNITY_RUN_ID \
     --out /path/to/new-review-packet
   ```

5. Record the chosen route and remaining work, then start the normal
   opportunity-backed quest for its workflow:

   ```sh
   python3 forge.py --home /path/to/workspace quest-start --run-id parts \
     --opportunity-run OPPORTUNITY_RUN_ID --workflow WORKFLOW_ID \
     --out /path/to/new-quest
   ```

   There is no automatic option-to-recipe compiler. The quest retains the
   workflow and source leads; the builder chooses modules, writes the adapter,
   reviews resources and rights, and maps promised needs in the recipe. See
   [the inventor's playbook](INVENTOR_PLAYBOOK.md) and
   [recipe preparation](QUEST_PREPARATION.md).
6. Freeze representative and adversarial cases and a manual or existing-tool
   baseline. Build the reviewed prototype, run it only when separately
   authorized, and assess its exact recorded output. Preserve failures and
   actual operator interventions.
7. Revise the bundle with new evidence and use `opportunity-compare` to retain the
   changed reasoning. A sufficient existing solution should close or redirect
   the new-build proposal.

Historical reports without options remain readable without invented new results.
After changing Forge source, replay inputs and selected corpora in a fresh
workspace under the normal source-binding rules.

See [Opportunity Lab's schema](OPPORTUNITY_SPEC.md),
[product direction](PRODUCT_DIRECTION.md) and
[quest assessment](QUEST_ASSESSMENT.md) for the surrounding workflow.
