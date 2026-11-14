# A reviewed evaluator for one fixed experiment

The generic behavioral review checks declared contracts, artifacts and recorded
observations. The reviewed evaluator adds one named execution path and a checked
translation of its raw report. It reuses `contract_trial.external_vectors()` for
the existing RFC 8785 examples, rather than accepting executable instructions from
an opportunity, source file or report.

## Scope

The two fixed subjects are `forge_core.common.canonical` and the pinned vendor
`forge_core._vendor.rfc8785._impl.dumps`. Each has its own actual indexed code lead
and behavioral contract. The fixed inputs are 26 numeric rows from
`contracts/interop/rfc8785_numbers.json` and two object rows from
`contracts/interop/rfc8785_objects.json`: 28 observations per subject, 56 in a
complete execution. RFC-B-10 and RFC-B-11 expect nonfinite-number rejection; the
other 26 cases require specific serialized output.

The baseline is FORGE's deterministic metadata serializer; its job is not to
implement every RFC 8785 serialization rule. A mismatch here identifies a
contract difference for this proposed use. The trial does not replace the
metadata serializer or invalidate the hashes of existing FORGE records.

These are previously published, previously visible examples. They exercise a
bounded serialization contract, not arbitrary application behavior, all of RFC
8785, a newly discovered mechanism or an independently certified implementation.
The older contract trial's Node comparison, admission checks and handoff demo are
separate and are not silently included in this experiment.

## Three operations

The [runnable example](../examples/reviewed_evaluator/README.md) gives complete CLI
commands. Keep the checkout, workspace, prepared packet and result packet separate.
Neither packet may contain the other or the workspace. Outputs must be new, and
symlink paths and existing non-directory ancestors are refused before writing or
starting a component. Original source roots already indexed in the same workspace
are also protected from trial outputs.

1. `reviewed-trial-prepare` freezes the real source leads, input artifacts, complete
   fixture mapping, two acceptance contracts, evaluator/helper bindings and local
   environment. It records `REVIEWED_TRIAL_PREPARED`; no trial input is evaluated.
2. `reviewed-trial-run` checks the prepared inputs against current bytes and the
   current environment, writes a start receipt, and invokes the fixed evaluator
   once in a fresh bounded Python process. It records the child's actual runtime
   and module identities, preserves the raw report, rechecks required source bytes
   and derives the behavioral observations. There is no automatic retry or caller
   override for executable, module, callable, cases, assertions or harness.
3. `reviewed-trial-verify` checks exact packet membership, hashes, identities and
   schemas, then recomputes the mapping, translation and behavioral comparisons.
   It neither creates a workspace nor evaluates the trial inputs. It deliberately
   does not call the older `contract_trial.verify_packet`, whose interop receipt
   check performs serialization again.

Ordinary internal JSON metadata encoding still uses FORGE's serializer. The
no-execution statement for preparation/verification refers to the fixed trial and
the selected vendor component; it does not claim that the program runs no Python.

## How the report becomes evidence

Numeric inputs retain their exact IEEE-754 hexadecimal representation. Their
positive results compare serialized strings exactly: `"0.0"` fails an expected
`"0"`, even though the numbers are equal. Object outputs compare exact bytes,
represented as hex. The adapter does not parse and reserialize an output to make
an incorrect spelling, escaping choice or key order disappear.

The two negative cases require the declared exception class: `ValueError` for the
baseline and `FloatDomainError` for the vendor. An import error, timeout or an
arbitrary error string is not a successful domain rejection. Positive cases stop
a component that rejects every input from appearing suitable.

The adapter validates the complete fixed case inventory, subject names, input
mapping and expected values. It derives observations from `actual`, `error` or
`actual_hex`. Existing `passed` flags and totals must agree with the derived
values; they cannot supply the result. Unexpected fields, duplicate/unknown cases,
malformed or contradictory output/error pairs and altered expectations are refused.
The raw report remains a separate output artifact bound to the prepared contract;
future result hashes are never inserted into the pre-run acceptance identity.

## Failure and exit behavior

| Status | Meaning | CLI exit |
|---|---|---|
| `REVIEWED_TRIAL_PREPARED` | Inputs and contracts saved; the trial has not run. | 0 |
| `REVIEWED_TRIAL_COMPLETED` | A complete raw report was collected and compared. Each subject retains its own `REPORTED_PASS` or `REPORTED_FAIL`. | 0 |
| `REVIEWED_TRIAL_EXECUTION_FAILED` | Execution did not yield a valid complete report. Its error and available evidence are retained; missing observations are not invented. | 2 |
| `REVIEWED_TRIAL_VERIFIED` | The stored packet is internally consistent, including an intact failed execution or a completed experiment with failing cases. Inspect its qualification and comparisons. | 0 |
| `BLOCKED` | An unsafe layout, changed binding, malformed input or inconsistent evidence prevented the operation. | 2 |

Completion and evidence integrity are different from behavioral success. A run
with genuine wrong outputs remains useful failed evidence. If the collector
raises before it returns a complete report, the packet records that limitation;
it does not pretend that every case ran, failed or remained unexecuted. Existing
packets are never overwritten to hide an earlier outcome.

If an unexpected bytecode-cache file appears, bounded regular files are retained
as opaque failure evidence and are never loaded by verification. Unsafe or
over-budget content can prevent sealing a packet. In that case the captured raw
output, start/error records and an explicit incomplete-packet marker remain; the
command is blocked and the partial folder must not be called verified evidence.

## What the checks establish

The fixed adapter can establish local agreement between pinned fixture bytes,
case inputs, raw observations, translated records and recomputed comparisons. It
checks the workflow's source/environment bindings and local sequence records.
Those are narrow facts about this saved experiment, kept separate from the generic
behavioral review's authority fields, which remain false.

A fresh interpreter and checked module origins reduce contamination from an
already-loaded Python process. The actual child environment is recorded, with
bounded fields rather than a dump of process variables. This is reviewed fixed
code running on a trusted local host; it is not a sandbox for hostile code.
Source hashes and local timestamps cannot authenticate who executed a process,
prove an external chronology, establish complete dependency coverage or prevent
a controlling local user from fabricating a self-consistent packet. A passing
comparison grants no automatic adoption, license clearance or release approval.

The existing opportunity and transfer-review results remain unchanged. The trial
supplies evidence for its specified role; it does not make supplied compatibility
descriptions true or remove unresolved prerequisites.
