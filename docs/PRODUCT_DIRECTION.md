# Product direction

Forge should help a person or supervising agent turn a documented problem into a
useful, tested product by combining existing knowledge and suitable components.
The outcome is a working intervention with evidence about what it accomplishes,
what remains unresolved, and the next decision.

Source preservation, static analysis, search, memory and packaging support that
purpose. Their value should be measured by how much they improve the complete
investigation and creation task.

## The creation loop

1. Record an observed task: who performs it, what they need to decide, their
   available inputs, the current workaround and its consequence.
2. Check existing alternatives and evidence that could invalidate the proposed
   gap. A limitation in one situation does not establish a broad market.
3. Find candidate mechanisms in authorized source material. Inspect complete
   modules, dependencies, interfaces and reuse conditions.
4. Choose a bounded intervention. Preserve the originating needs and define
   observable acceptance cases, including incorrect and uncertain inputs.
5. Build the smallest useful prototype with a reviewed adapter.
6. Run the authorized experiment on the exact archive, assess the recorded
   observations, and retain failures, omissions and unresolved needs.
7. Use the result to revise, stop or conduct a field comparison. Record why.

The agent supplies interpretation and implementation choices. Forge provides
deterministic records, inspection and consistency checks. Its analysis and
assessment commands do not start a model or execute inspected code.

## What is connected today

| Stage | Current implementation | Remaining limitation |
| --- | --- | --- |
| Observe a workflow | Opportunity Lab stores observations, needs, sources, alternatives and counterevidence | The operator or agent supplies and reviews the observations |
| Find mechanisms | Corpus intake, lexical search, source inspection and dependency context | Retrieval relevance is not demonstrated usefulness or complete dependency resolution |
| Preserve the product's purpose | An opportunity-backed creation quest retains the whole selected workflow and proposed responses | The proposed response still needs an experiment |
| Assemble a prototype | `quest-build` verifies whole Python modules from folders or ZIPs, explicit package paths, resources and notices, then packages the supplied entry point | The builder writes adapters, selects package data and dependencies, and reviews reuse rights |
| Assess recorded outcomes | `quest-assess` verifies declared case input bytes when supplied, compares fixed expected JSON with bound observations, and aggregates acceptance/need results | Artifact consistency does not authenticate external execution, input consumption or the meaning of a case |
| Choose what comes next | Assessment exposes failed, incomplete and unaddressed work with a next decision | A persistent investigation scheduler and practitioner feedback loop remain future work |

## Improvement order

### 1. Complete one useful creation loop

Keep the problem, alternatives, source identity, recipe, exact product, acceptance
cases, recorded results and next decision connected. Each promised need must show
its supporting cases. Missing evidence must remain visible even when other cases
pass. Preserve the original build receipt and every failed trial.

### 2. Measure improvement in the user's task

Compare the same representative task and inputs using the prototype and an
existing or manual workflow. Record correct outcomes, operator actions, time,
handling of missing information and integration effort. A passing software test
establishes behavior for its cases; practical advantage requires this comparison.
If an existing option already solves the task adequately, record that result and
reuse it or redirect the investigation.

### 3. Resume investigations from their outcomes

Connect assessed outcomes to the next unresolved question, evidence request,
budget and stop/revise/continue decision. Preserve contrary evidence across
restarts. A preference or owner's judgment must not convert an unsupported claim
into a successful result.

### 4. Improve retrieval against held-out tasks

Compare agent-plus-Forge work against ordinary repository search on unfamiliar
jobs. Measure useful candidates, time to a tested prototype, failed integrations
and constraint retention. Use observed failures to justify changes to query
expansion, mechanism selection and dependency context. Scout mode currently
samples definitions; it does not establish independent product invention.

### 5. Reduce repeated source preparation

Carry reviewed archive-member mappings, package resources, dependency assumptions
and required notices into assembly. Keep exact-byte source checks and the ability
to reconstruct the selected material. Splitting an arbitrary function from its
module does not establish that it can run independently.

## Completion criteria for a product trial

A useful trial record answers:

- What problem was observed, and for whom?
- Which existing alternatives were considered?
- Which components and adapter produced the exact artifact?
- Which fixed cases passed, failed or remain incomplete?
- Which original needs remain unaddressed?
- What did the comparison establish, and what evidence is still needed?
- Why should the next action be revision, further evaluation, a field trial or stopping?

See [the inventor's playbook](INVENTOR_PLAYBOOK.md),
[quest assessment](QUEST_ASSESSMENT.md), and
[the capability inventory](CAPABILITIES.md) for the current boundaries.
