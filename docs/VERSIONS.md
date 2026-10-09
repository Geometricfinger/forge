# Versions

Forge has **one release version**. Everything else with a version number is either a separate component, a data or schema format, or a historical label.

## Release version

| Item | Value | Source of truth |
|---|---|---|
| Forge release | **0.8.4** | `forge_core/__version__` in [`forge_core/__init__.py`](../forge_core/__init__.py) |

These read the release version from that one place: `python3 forge.py --version`, the web UI headers (Workbench, Opportunity Lab, Contract Lab), the GitHub discovery `User-Agent` (`FORGE-Workbench/0.8.4`) and new Opportunity review exports. To cut a release, change only `__version__`.

The default workspace folder `~/.forge-workbench-0.8.4` is a **workspace-layout name**, not the release version. It is defined separately (`forge_core.DEFAULT_HOME_NAME`) and stays the same when the release version changes, so existing workspaces keep working. A workspace is still bound to the exact Forge source that created it: after updating Forge, use a new `--home` if you see `WORKBENCH_CODE_CHANGED_NEW_HOME_REQUIRED`.

## Component versions

These are versioned independently and are recorded in their outputs so results can be tied to the exact analyzer that produced them. They are not release numbers.

| Component | Version | Defined in |
|---|---|---|
| Hound analyzer engine | 0.5 | `hound/hound.py` `VERSION` (also `hound/persistent_hunt.py`) |
| Hound baseline detectors | 0.1.0 | `hound/baseline_sniffers.py` `VERSION` |
| Exact-API probe add-on | 0.1.0 | `addon/tool/mission_probe.py` `VERSION` |
| Controlled improvement loop | 0.2.0 | `loop/forge_cycle.py` `VERSION` |
| Research runner | 0.1.0 | `runner/research_runner/__init__.py` `__version__` |
| Vendored RFC 8785 (JCS) library | 0.1.4 | `forge_core/_vendor/rfc8785/__init__.py` (upstream version) |

Earlier Hound and probe versions (0.2.0, 0.4.0 and the pre-scope-cache probe) are kept under `hound/reference/` and `addon/reference/` only as regression baselines.

The analyzer files themselves are identified by hash, not by version: `contracts/analyzer_lock.json` pins the `SHA256SUMS.txt` manifests of `hound/`, `addon/` and `loop/`.

## Algorithm and format identifiers

String identifiers such as `field-bm25-rrf-2-role-state` (`forge_core/retrieval.py`), `identifier-fields-1` (`lexical.py`), `lexical-context-2-class-state` (`structure.py`), `mixed-intake-2-context` (`mixed_intake.py`) and `opportunity-1.1-inventor-audit` (`opportunities.py`) name the behaviour of one algorithm. They are part of cache keys and run identities, so changing one invalidates the matching cached results. Integer `schema` / `schema_version` fields version individual JSON formats.

## Historical labels

- `contracts/release_contract.json` (`"workbench_version": "0.8"`, 563 tests) is a frozen acceptance contract from the 0.8 series. It is kept unchanged as evidence and does not describe the current release.
- [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md) was first written at internal milestone 0.4. Its title no longer carries a version.
- Earlier UI builds showed 0.8.2 and the GitHub `User-Agent` said 0.3. Both now read the release version.
