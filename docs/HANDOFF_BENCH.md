# Handoff bench: export evidence and total work

The handoff bench is an isolated executable example under
`examples/handoff_bench/`. It adds deterministic export-to-model adapters and a
burden ledger around the preserved evidence-recovery planner. It does not alter
FORGE's analysis commands, add network access, introduce an adaptive objective
or infer histories from scans.

## Contracts and ownership

| Concern | Contract or component |
| --- | --- |
| Unchanged planning objective and validation | `examples/handoff_bench/SPEC.md` and `planner_core.py` |
| Handoff input, extraction, filtering and accounting | `examples/handoff_bench/HANDOFF_SPEC.md` |
| Documentation support and unknown format details | `examples/handoff_bench/SOURCE_FIELDS.json` |
| Preserved original bytes | `examples/handoff_bench/preservation_pins.json` |
| Whole permitted modules and notices | `examples/handoff_bench/SOURCE_PROVENANCE.json` and `vendor/` |
| Existing-report augmentation | `examples/handoff_bench/report.py` |

The builder checks preserved identities before packaging. The same archive
accepts the legacy planner batch or `--mode handoff`. Runtime and data-only
verification are explicit operations; packaging does not execute indexed source.

## Handoff model

A handoff input names its case and source route, references raw artifacts,
supplies reviewer decisions as explicit histories, and supplies questions,
budget, burden events and any recorded follow-up. Every history retains the
original decision and answer mapping. Every question retains its original
label, cost and availability. Additional source references support review;
they are not authority to invent domain facts.

Artifacts are files relative to the handoff input, with exact sizes and hashes.
The reader refuses traversal, symlinks, changed content and declared bounds
violations. The complete contract defines supported kinds and native manifest
keys. No downloaded artifact, archive executable or plugin is invoked.

The filtering rule removes a supplied history only when both its condition and
the export observation are known and different. A missing observation or
condition cannot establish a difference. This preserves supplied alternatives;
it never creates a Cartesian product of new histories. Dates remain opaque
strings. Case labels are not assumed globally unique.

An inconsistent observation set or zero retained histories blocks the
export-derived model. It cannot enter the optimizer as an invented single world.
The original manual model remains available for comparison. A nonempty,
consistent retained set is converted into exactly the existing planner's
`{schema: 1, cases: [...]}` input format and evaluated by its unchanged
validation and solving functions.

## Exact parser claims

### Medit

The official [Get Result Files guide](https://support.medit.com/hc/en-us/articles/360021126491--Get-Result-Files)
lists the case-list content items, permits exporting the displayed subset,
and describes the PDF option and automatic dentalProject creation for an
exocad export. The bench's eight CSV labels are listed in
[SOURCE_FIELDS.json](../examples/handoff_bench/SOURCE_FIELDS.json).

These are documented content items, not a versioned byte-format specification.
The fixture profile is explicitly bounded: preserve matching nonempty values as
strings, retain missing and unrecognized data as unknown, and expose conflicting
matching values. It does not parse dates or extract implant metadata from free
text. Actual vendor dialects, localization and version compatibility require
permitted samples.

The PDF and dentalProject layouts were not established by the reviewed public
documentation. Their artifact presence does not prove a particular field
survived. Keeping those fields unknown is a supported outcome.

Medit's [storage guide](https://support.medit.com/hc/en-us/articles/40300986213913--Guide-Scan-Data-and-Storage-Management)
distinguishes ordinary geometry export from complete case information. It
supports testing route differences without assuming every other route loses the
same information.

### 3Shape

The official [Unite export guide](https://support.3shape.com/products-unite-software-how-to/export-scans-in-unite)
describes 3oxz as a native case/order format with scans, screenshots, printable
forms and patient information; STL/PLY are scan routes. It does not specify a
native byte signature, member layout or field serialization.

The bench therefore consumes a clearly identified reviewer-created manifest.
Its category observations cite exact text ranges in pinned reviewer artifacts.
Those observations retain human interpretation and support only declared
presence of a documented content category. They do not decode 3oxz bytes or
authenticate a vendor package.

An outer native source label, a filename extension or a mesh cannot supply
administrative evidence. An STL/PLY-only input stays scan-only. Unknown fields
and incomplete native interpretation remain visible in both outcomes and burden.

## Decisions and follow-up

The three comparison routes are:

- **Manual model:** the originally supplied histories and questions.
- **Export reconciliation:** the supplied histories consistent with export
  observations, before additional questions.
- **Export plus planner:** that same retained model evaluated by the unchanged
  planner, with only explicit recorded follow-up allowed to narrow it further.

A decision is resolved when all surviving supplied histories agree. A
recommended question plan alone leaves the decision unresolved. A budget
blocker or insufficient evidence remains visible. Questions planned, actually
recorded and simulated are separate counts.

Optional follow-up must cite a pinned response, identify an existing available
question and connect to a ledger event. Unknown historical answers remain
possible. Responses do not generate histories and are never sent automatically.

The verifier's frozen synthetic truth stays outside the product input.
Correctness is scored only when a produced decision and retained history agree
with that independent expectation. An unresolved decision or unavailable truth
has unknown correctness. This is synthetic administrative scoring, not clinical
validation.

## Burden is not planner cost

The ledger records every acquisition, interpretation, model-preparation and
follow-up event with actor, measurement class, integer nanoseconds, step count
and evidence source. It also records route-specific unresolved counts.
Declared planner effort units are unchanged and are never added to durations.

Human acquisition and interpretation cannot be inferred from local file-read
time. Missing necessary human events receive explicit unknown coverage.
Measured adapter durations, observed human durations, reported durations and
synthetic durations remain separately summed.

`known_wall_time_ns` is the sum of known values, regardless of measurement
class. It is a subtotal. `wall_time_ns` remains null when coverage is incomplete.
`complete` means numeric coverage; `observed_complete` also requires observed
provenance. A fully declared synthetic ledger may add correctly while supporting
no claim of measured human savings.

Optional route allocations charge each shared event once within every
applicable hypothetical route. The global total counts each event once.
Allocated route totals are not independent timed trials. Sequential event sums
measure recorded work burden and should not be read as elapsed makespan when
real work overlaps.

## Evidence and publication

The external supervisor and data-only verifier use one bound execution contract
for legacy and handoff inputs. Product identity, preserved planner/component
bytes, specifications, inputs, exact commands, declared resource limits,
before/after observations and output sidecars must remain mutually consistent.
A changed product, specification or resource limit must fail verification even
when mathematical output is unchanged.

Output size checks run before writing a new result directory. Existing output
is refused. File hashes establish byte consistency and do not authenticate
execution, chronology, actual source authenticity or the completeness of the
reviewer's world model. Resource supervision is not filesystem or network
isolation.

Only synthetic fixtures belong in public evidence. Real exports require a
separate permitted review and must not be added to this public example.
Qualification, source/history publication checks and generated-evidence checks
remain required.

## Static report extension

`augment_report(existing_html, benchmark)` inserts or replaces one bounded,
escaped static section. It preserves the surrounding report and adds no new
JavaScript, stylesheet, application or workflow screen. Its comparison shows the
same handoff across all three routes, independent correctness or unknown,
unresolved count and planned/recorded/synthetic question counts.

Measured adapter time is displayed separately from other duration classes.
Incomplete total burden remains unknown. Shared allocations are labeled, and
the report does not derive a winner from missing or synthetic timing. The raw
bench records remain the authority for every displayed case.

A passing synthetic bench establishes implementation behavior at these declared
boundaries. Genuine export compatibility, representative reviewer burden and
a useful market gap require additional observed evidence.

