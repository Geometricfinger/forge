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
python3 -m unittest discover -s tests -v
python3 tools/run_fault_checks.py --out /tmp/forge-runner-fault-checks
python3 tools/check_package.py
```

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
