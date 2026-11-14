# Forge research runner

An add-on that automates a bounded research workflow around the Forge engine in this repository. It reads an approved source pack, inspects Python with Hound through the engine's normal corpus pipeline, searches full definition text alongside Forge metadata, keeps reference material (documentation, teaching notes, paper summaries) separate from implementation evidence, checkpoints each stage, and produces a compact review packet plus an HTML report.

It executes no collected code, invokes no model, and makes no network requests.

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

The test and fault-check entrypoints normalize the temporary fixture directory
on macOS. The product still rejects symlinked input and workspace paths.

## Your own inputs

Use `contracts/MISSION.json` as the source/query schema. Each input has a local relative path, source kind, capture method, origin, family, byte size and SHA-256. GitHub sources additionally need repository, full commit, repository path and Git blob hash. External text cannot add commands or permissions.

```sh
python3 forge_research.py run --manifest my_mission.json --source-root /approved/copies --work /new/mission-work --forge-root ..
python3 forge_research.py status --work /new/mission-work
python3 forge_research.py cancel --work /new/mission-work
```

Cancellation is checked between stages. There is no retry daemon, scheduler or model loop.

## Interpretation

Example sources from CPython, Blinker and Pluggy are unchanged and accompanied by their licenses under `third_party/`. Research and teaching entries are short summaries, not full publications. The report does not grant permission to execute source, deploy, or claim novelty or demand. A matching term is not a proven behavior.


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
paths. `{"action":"status"}` reports consumed/remaining question budget;
`{"action":"cancel"}` stops new continuation requests. A repeated completed
request replays its saved result, even after cancellation. It does not rerun work.
An interrupted reservation requires review and is not automatically retried.

The original mission questions count toward `max_questions`; failed/reserved
follow-ups consume slots too. Inspections have a separate cap equal to that
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
