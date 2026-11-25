# Forge research runner

An add-on that automates a bounded research workflow around the Forge engine in this repository. It reads an approved source pack, inspects Python with Hound through the engine's normal corpus pipeline, searches full definition text alongside Forge metadata, keeps reference material (documentation, teaching notes, paper summaries) separate from implementation evidence, checkpoints each stage, and produces a compact review packet plus an HTML report.

It executes no collected code, invokes no model, and makes no network requests.

## Context for a conversation or agent

`forge_converse.py` gives a trusted local client persistent facts, a record of
played speech and optional excerpts from a completed research mission. The host
chooses the user, database and project profile before sending any request. These
identifiers assume an already trusted local account. The client supplies speech
recognition, dialogue policy and audio playback.

For microphone input, launch with `--channel speech`. This permits a context
read or a proposed fact change. To suggest a change send
`{"action":"propose","proposal_id":"change-1","key":"location","value":"north entrance"}`.
Use JSON null for a proposed deletion. From a separate host control, launch
with `--channel review` and send `{"action":"preview","proposal_id":"change-1"}` to
show the pending key and value, including after a restart. After a deliberate
decision, send `{"action":"decide","proposal_id":"change-1","accept":true}`
or set `accept` to false. Replayed decisions preserve the outcome. An intervening
write to the same fact makes pending suggestions stale, including a change away
from and back to the original value. Allocate a fresh ID for a new suggestion.
The host selects the channel and identity outside the transcript and request
payload. Use the default host channel for trusted application commands.

Save `{"project":"workshop"}` in `profile.json`. From this directory, send one
JSON object per invocation. The database can be reopened in a later process.

```sh
echo '{"action":"remember","key":"location","value":"north entrance"}' |
  python3 forge_converse.py --db ./private/memory.db --user operator --profile profile.json
echo '{"action":"context","keys":["location"]}' |
  python3 forge_converse.py --db ./private/memory.db --user operator --profile profile.json
```

Place the database outside shared or published directories. Operations use the
active user and project. `correct` compares `old_value` before applying
`new_value`. `forget` removes a key from fact recall. `clear_scope` clears
that scope's facts and recorded playback together. Mission ledgers have their
own retention and may contain search queries.

For speech, send `start` with a unique `turn_id`, then `acknowledge` with the
turn ID, consecutive 1-based `sequence` and exact `text` after each complete
segment plays. Preserve spaces between segments. Send `finish` with
`interrupted` set to true or false. `context` returns the latest turn and its
confirmed spoken prefix. Starting another turn supersedes a still-playing
turn. Retries of an acknowledged segment preserve its original text.

For source lookup, add a `mission` to the trusted profile:

```json
{"project":"workshop","mission":{"manifest":"mission.json","source_root":"sources","work":"completed-run","sources":["handbook"]}}
```

Paths resolve relative to that profile. Use a completed research run and
source IDs in its manifest. A `context` request accepts a `query` and optional
`anchors`, for example `{"action":"context","query":"pickup point","anchors":["north entrance"]}`.
Each fresh search uses the supervisor's existing follow-up allowance and
inspects at most three ranked reference hits, up to 1,600 characters apiece.
Repeated identical lookups in the same scope reuse their receipts. The
result includes the source origin, digest, indexed line range and any
truncation. Anchors filter the returned text by whole words or phrases.
Treat a match as a passage to interpret. An unmatched or blocked lookup
leaves the question open. Source text cannot select another project or write
a remembered fact through this interface.

## Run the offline example

```sh
python3 run_demo.py --out /tmp/forge-research-demo
```

The runner verifies the engine's analyzer manifests (`../contracts/analyzer_lock.json`), reads the bundled sources in `examples/sources/` as data, runs the 24-question scenario in `contracts/MISSION.json`, and writes `mission/Review.html`, `mission/report.json` and `mission/supervisor_packet.json`. A bounded run and continuation:

```sh
python3 run_demo.py --out /tmp/forge-research-demo --steps 2
python3 run_demo.py --out /tmp/forge-research-demo
```

A paused run returns a non-zero exit code with a `PAUSED_BUDGET` report. A completed run reuses its checkpoints after verifying source hashes.

## Tests

```sh
python3 run_tests.py
python3 tools/run_fault_checks.py --out /tmp/forge-runner-fault-checks
python3 tools/check_package.py
```

The package check verifies SHA-256 hashes for the runner files. In a Forge Git
checkout it also compares the manifest with all tracked files under `runner/`,
excluding the manifest itself. Untracked notes and local outputs are outside this
inventory. In an extracted archive
the checker verifies the listed files and reports `MANIFEST_ENTRIES_ONLY`.

After editing the runner, stage any new package files, run
`python3 tools/check_package.py --refresh` from this directory, and review and
stage `FILE_HASHES.json`. The refresh reads the working copies of tracked files.
Refresh refuses to replace an existing digest for a changed or removed file under
`contracts/`. Those files record historical commitments.

The test and fault-check entrypoints normalize the temporary fixture directory
on macOS. The product still rejects symlinked input and workspace paths.

## Your own inputs

Use `contracts/MISSION.json` as the source/query schema. Each input has a local relative path, source kind, capture method, origin, family, byte size and SHA-256. GitHub sources additionally need repository, full commit, repository path and Git blob hash. External text cannot add commands or permissions.

Code and mixed questions must also satisfy the selected Workbench's actual search
policy: at most 200 characters, no control characters, and 1–32 distinct searchable
terms after its tokenizer. Initial missions validate this before creating their
workspace. Reference-only questions retain the separate 400-character manifest
contract. An optional `required_api` uses observed API evidence and is applied
before either code-search channel's result limit; mentioning an API in prose is
not an observation. Observations come from the bound corpus, including when a
source-text query matches a name that is absent from the metadata search fields.

```sh
python3 forge_research.py run --manifest my_mission.json --source-root /approved/copies --work /new/mission-work --forge-root ..
python3 forge_research.py status --work /new/mission-work
python3 forge_research.py cancel --work /new/mission-work
```

Cancellation is checked between stages. There is no retry daemon, scheduler or model loop.

## Interpretation

Example sources from CPython, Blinker and Pluggy are unchanged and accompanied by their licenses under `third_party/`. Research and teaching entries are short summaries, not full publications. The report does not grant permission to execute source, deploy, or claim novelty or demand. A matching term is not a proven behavior.


### Callable and module evidence

Research retains both function definitions and the core corpus's supported
module/script records. Results, compact packets and inspected evidence carry
`record_kind`: `function` or `source_scope`. A module record uses the core's
source occurrence identity, has the name `<module>`, and is never presented as a
function. Source summaries count `definition_count` and `source_scope_count`
separately. The corpus still determines which module records are available;
this does not add another parser or infer API calls from source text.

A `source_scope` record contains the complete containing Python segment,
including surrounding declarations and any nested functions. Its observed APIs
are the corpus's observations for that module scope. The retained UTF-8 source
window has exact offsets and hashes, just as function windows do. The existing
250,000-byte per-window and 25,000,000-character per-source retention budgets
still apply. Previously supported functions receive retention space first;
module context that cannot fit receives `SOURCE_SCOPE_RETENTION_LIMIT`.
Larger eligible modules receive `SOURCE_SCOPE_SIZE_LIMIT` in the source gaps
instead of a prefix labeled complete. Unsupported languages remain text-only
references, and retained source remains private in the Runner workspace; the
core corpus still stores no source bodies.

Search also retains the core's bounded terms and source locations for a method's
direct enclosing class description. Intake compares this declaration metadata
between the fresh parse and the checksum-verified corpus; queries check it again
against that corpus. The words can match in both search channels, while
`class_documentation_match` identifies their class, matched terms and segment-local
documentation lines in results, compact packets and supervisor answers. This is
declared context, not evidence that every method implements the description.
No raw class docstring is reconstructed. Nested functions and overriding methods
do not inherit another class's description. Missing older metadata adds no new
class terms. Added class words count toward the existing retained-search budget.

Code search also shares the Workbench's bounded spelling expansions, explicit
conversion direction, declared return names, and subject-matching predicate cues. A forward declaration
can outrank an explicitly reverse declaration even when the latter contains more
query words. The fused result exposes `directional_match`, `directional_conflict`,
`predicate_match`, and `declared_output` when present; query evidence identifies the top declaration.
These fields come from the bound corpus, not an API claim in source text.
Ordinary complete term coverage retains priority except for a reverse direction
or different declared return name when a matching declaration is available.
Alias and subtype relationships are not established. Arbitrary requirements
(including date-only parsing or absence of side effects) still require review and
acceptance tests. See [search intent and limits](../docs/SEARCH_INTENT.md).

The added result fields retain report schema 1. Runner code is part of mission
identity, so existing completed missions must be recreated in a fresh external
workspace after this update; do not rewrite their binding or checkpoint files.

## Supervisor continuation

`forge_supervise.py` continues a completed, source-bound mission. It calls the
existing retrieval functions, not another intake, model, network service, shell,
or source execution endpoint. The trusted operator supplies questions and judgment.

```sh
python3 runner/forge_supervise.py \
  --forge-root /absolute/path/to/forge \
  --manifest /absolute/path/to/mission.json \
  --source-root /absolute/path/to/source-pack \
  --work /absolute/path/to/completed-mission \
  --request /absolute/path/to/request.json
```

Requests are exact data-only objects (unknown fields are rejected):

```json
{"action":"ask","request_id":"followup-1","question":{"id":"followup-1","query":"hash function","intent":"code","sources":["source-id-from-manifest"]}}
```

`inspect` requires `request_id`, `source_id`, an `evidence_id` returned by the
existing index, and `max_chars` from 16 to 4096. It does not accept arbitrary file
paths. Function and module inspections preserve `record_kind` and the bound
`source_window`; `truncated` describes the returned excerpt, while the window's
hash and byte count describe the complete indexed source.
`{"action":"status"}` reports consumed/remaining question budget;
`{"action":"cancel"}` stops new continuation requests. A repeated completed
request replays its saved result, even after cancellation. It does not rerun work.
An interrupted reservation requires review and is not automatically retried.

The original mission questions count toward `max_questions`; failed/reserved
follow-ups consume slots too. An invalid code-search query or API filter is
rejected before a follow-up is reserved, without changing its question budget,
completed reports or supervision ledger. Inspections have a separate cap equal to that
original question budget. Request JSON is limited to 16 KiB. Full results stay in
`work/supervision/`; responses are bounded by the original packet budget. Missing
inputs or altered source, report, checkpoints, runner code or workbench code block
continuation. Cancellation is cooperative admission control, not a process-kill
or sandbox guarantee. A local trusted account controls the ledger and hashes;
they are not independent attestation.

### Binding revision

Mission binding now includes a digest of workbench source and configuration,
not only the analyzer manifests. Older missions without this digest must be
recreated in a fresh external work directory. Do not edit old binding files to
pretend they were qualified under the new code. Source originals stay unchanged.
