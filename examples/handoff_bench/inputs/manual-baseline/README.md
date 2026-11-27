# Frozen independent manual baseline: minimal evidence recovery

This baseline was authored by the independent `invention_challenger` agent before seeing or executing a product implementation. It is a set of hand-labeled synthetic mathematical cases, not a practitioner study, clinical evaluation, or measured record of real organizations.

## Frozen problem

A reviewer supplies a finite set of possible histories (`worlds`). Each world declares a decision and known answers to some candidate questions. Questions have a declared nonnegative integer effort cost and availability. The task is to select a nonadaptive set of available questions that distinguishes every pair of worlds requiring different decisions. A question distinguishes a pair only when both worlds explicitly have a string answer and those strings differ. Missing answers are unknown and cannot establish a guaranteed distinction.

The optimum minimizes total declared cost first, then the number of questions. Retain at most eight equally optimal question sets, ordered lexicographically by their sorted question IDs; retain the exact number omitted. Question IDs and world IDs are identifier labels, not substantive evidence.

- If no pair requires different decisions, status is `NO_QUESTION_NEEDED`. The mathematical optimum is one empty set, cost zero and cardinality zero.
- If any cross-decision pair lacks a distinguishing available question, status is `INSUFFICIENT_EVIDENCE`. Return the exact sorted indistinguishable pairs; minimum cost/cardinality are undefined (`null`) and there are zero feasible plans.
- If separation is feasible but the minimum cost exceeds `max_cost`, status is `BUDGET_BLOCKED`. Retain the lower required cost. The oracle includes the mathematical optimum as a label, but it must not be presented as an actionable plan within the supplied budget.
- Otherwise status is `PLAN_FOUND`.

These are questions that would distinguish the reviewer-declared alternatives assuming the supplied answer model; they do not resolve any real case, send requests, execute a repair, authenticate records, infer clinical facts, or establish that the world's alternatives are exhaustive.

## Input contract

One case has exactly:

```json
{
  "schema": 1,
  "case_id": "C01",
  "worlds": [{"id": "w1", "decision": "HOLD", "answers": {"question_id": "known answer"}}],
  "questions": [{"id": "question_id", "label": "Question label", "cost": 1, "available": true}],
  "max_cost": 1
}
```

Limits supplied to the implementation agent before labels were exposed: 1–24 worlds, 0–16 questions, integer costs from 0 to 1,000,000, integer maximum cost from 0 to 16,000,000, no duplicate IDs, no answer keys referencing undefined questions, no extra fields. IDs are 1–80 ASCII characters from `[A-Za-z0-9_-]`. Decision and answer strings are Unicode scalar strings of at most 500 characters. Empty answer strings are known; missing keys alone mean unknown. At most 64 cases in a batch. The adapter's malformed-input/refusal controls are separate from these 24 semantic cases.

`input-batch.json` contains `{schema: 1, cases: [...]}`. The `inputs` directory repeats each case independently. `expected.json` has the manual results, explanation and naïve baseline. Costs are declared synthetic effort units, not observed seconds or money.

## Label method and separation

`build_manual_baseline.py` serializes explicitly supplied labels; it does not contain an optimization solver. The author reasoned about small cases: mandatory separating questions, duplicate separators, three equally optimal pairs, and ten identical singleton separators. Its assertions check hand-supplied ask-all sums and counts, and lexical ordering, without solving for optimum.

Implementation agents may read the input contract before implementation. The root agent agreed to keep expected labels hidden until after the first product outputs. `freeze.json` pins the exact serialized inputs, labels, rationale, constructor and this README. The local time and hash are consistency evidence from a trusted workspace, not independently authenticated chronology.

The baseline has no clinical labels. Synthetic decisions such as A/B/C, HOLD and REVIEW are opaque classes used to test decision separation. C23 includes Unicode and empty-string handling. C19 is a renamed/reordered copy of C04 for invariance; this deliberate relation is disclosed.

## Naïve baseline

Ask every available question. Its declared cost is the sum of all available question costs and its count is their number. Its sufficiency is whether that complete available set distinguishes every cross-decision pair. This baseline may ask unnecessary questions, exceed budget, or remain insufficient. It is a transparent automated comparison, not an estimate of how a skilled human would actually work.

The independent manual oracle separately supplies optimal decisions/plans. Neither baseline supplies human timing or validates actual field usefulness.

## Provenance and rationale

The challenge follows the user's goal of FORGE generating possible build options from gaps in existing offerings. Public official product research found broad existing capabilities in change management (Autodesk, Onshape, OpenBOM), portable technical data packages (Anark), version-bound instructions (Tulip), process intelligence (Celonis), and invention assistance (Patsnap and Iprova). Those findings motivated a narrower hypothesis: the potentially useful missing decision could be which evidence to request next, rather than another generic tracker or checklist.

That hypothesis remains unvalidated. The finite-world selection problem is a bounded technical test of one mechanism, not proof that a product category is absent. Product research URLs and supported/unknown distinctions are retained separately in `../../research/challenger.md`.

## Reproduction

Running `python3 -B build_manual_baseline.py` reserializes the same case and expected bytes, but creates a new timestamp and hash for `freeze.json`. Preserve the original freeze when auditing chronology; do not overwrite it as a routine evaluation step. A verifier should recompute the listed file digests and compare to the original manifest.
