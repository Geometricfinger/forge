# Evidence recovery experiment — frozen behavioral contract

Date: November 25, 2026 (America/Kentucky/Louisville).

The input is a finite set of reviewer-supplied possible histories (`worlds`).
Each history names the administrative `decision` it would require and any known
answers to available questions. Missing answer keys mean unknown. No possible
history is inferred from a mesh, a filename or a timestamp. The history set is
not presumed exhaustive in reality.

A question distinguishes two histories only when both have known answers and
those answers differ. Only pairs that require different decisions need to be
distinguished. An empty string is a known answer. Unknown is not a wildcard
answer that can be assumed favorable. Questions marked unavailable cannot be
used. Nonnegative integer costs are synthetic declared effort units.

The target is a fixed batch of questions that distinguishes every cross-decision
pair. Enumerate the finite question sets and minimize total declared cost, then
question count. Retain up to eight lexicographically sorted tied optimal sets,
with the exact full count and omitted count. This is a fixed-batch objective;
it is not an adaptive decision tree or an expected-cost estimate.

Statuses:

- `NO_QUESTION_NEEDED`: every supplied history requires the same decision. The
  single optimal set is empty, cost and cardinality zero.
- `INSUFFICIENT_EVIDENCE`: at least one cross-decision pair cannot be
  distinguished by any available question with known answers. Retain all such
  pairs and report no finite optimum.
- `BUDGET_BLOCKED`: a finite optimum exists but exceeds the declared budget.
  Retain its mathematical cost/sets as diagnostic evidence. No plan enters
  `plans_to_review`.
- `QUESTIONS_FOR_REVIEW`: at least one minimum-cost distinguishing set fits the
  budget. It needs review; no question is sent and no history is declared true.

Input limits: 1–24 histories and 0–16 questions per case; 1–64 cases per batch;
262,144 total possible subsets per batch; 262,144 input bytes. An integer cost
is 0–1,000,000, and the budget is 0–16,000,000. IDs are unique ASCII letters,
digits, underscore or hyphen, at most 80 characters. Text must consist of valid
Unicode scalar values and have at most 500 characters. Reject duplicate JSON
keys, unknown fields/question keys, invalid types, nonfinite numbers and bounds
violations. Validation precedes all solving. Output is JSON on stdout; the
prototype writes no user input or application file.

The independent manual baseline is authored by a separate agent, frozen before
prototype execution, and kept unread by the implementation author until the
first output exists. It is not a practitioner study. Compare exact mathematical
labels and a declared ask-all-available baseline on identical inputs. Cost or
request reductions are model results, not observed human savings. Preserve all
first-run disagreements, including baseline defects.

The adapter is agent-authored. FORGE preserves whole reviewed modules and source
identities, packages the adapter and records a fixed evaluation plan. The
unchanged `powerset` recipe supplies bounded subset enumeration; `bucketize`
groups histories by decision. Neither component provides domain interpretation
or independently establishes an opportunity. The prototype has no authority to
identify an implant, clear a clinical case, or change production.
