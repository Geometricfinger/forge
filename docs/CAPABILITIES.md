# FORGE capability inventory and verification boundaries

This map describes the public checkout, not a promise that every historical
proposal was implemented. Every generic capability belongs in the open project;
private application source, data, corpus metadata and credentials do not.

## Shipped and callable

| Capability | Entry point | Qualification / boundary |
|---|---|---|
| Python structure and exact API observations | hound, addon, corpus-run | Static parsing, not runtime meaning; Python only |
| Files, ZIPs, notebooks, Python fences | corpus-run, make-manifest | Bounded intake; explicit unread/blocked/unsupported states |
| Source pinning, recovery, lexical search and dependency context | corpus-* | File/version identity, checkpoints; not universal semantic search |
| Large Python module structure and complete definition inspection | corpus-run, corpus-context, corpus-inspect | Bounded 2 MB input, complete top-level units with shared module context and explicit omissions; unchanged 250 kB API analyzer limit; see LARGE_MODULES.md |
| Lossless source block reconstruction | source_blocks, corpus-run | Exact original-byte roundtrip and block hashes; bodies transient, hashes cannot recreate missing bytes |
| Optional bounded public GitHub discovery | new, packet, accept, run | Operator-started GET-only discovery; never implicit in local scans |
| Source-linked reuse review | review-* | Requirements and notes; no automatic license clearance |
| Rule-based opportunity analysis and feedback | opportunity-* | Reviewed annotations; not autonomous invention or customer validation |
| Declared contract transfer review | opportunity-review | Generic later adapter integrated here; preserves raw result, no authority |
| Source-bound behavioral plans and recorded evidence review | opportunity-plan, opportunity-review, behavior-result-template | Fixed operator assertions, actual selected artifact hashes and explicit pass/fail/incomplete states; no arbitrary execution or external execution authentication; see BEHAVIORAL_EVIDENCE.md |
| Controlled improvement loop | loop/, cycle-demo | Data-only profiles and fixed tests; not arbitrary self-modifying application execution |
| JSON contract trial and cross-runtime comparison | contract-*, interop-* | Named reviewed component; not universal runtime equivalence |
| Multi-source research missions | runner/forge_research.py | Bounded graph, packet, replay and cooperative cancellation |
| Research follow-ups and indexed evidence inspection | runner/forge_supervise.py | Later generic continuation integrated here; no model connection or shell |
| Local operator UI | serve | Loopback only; transfer review/continuation are currently CLI interfaces |
| Public packaging and CI | verify.py, runner/run_tests.py, prepublish_check.py | Synthetic public fixtures; exact source checks, not independent audit |

All 33 workbench Python modules from the preserved private 0.8.4 baseline remain
represented in the public core. The public layout unpacks the analyzer, adds local
folder intake and improves CLI/CI/portability. Comparing filenames and tests alone
does not establish equivalence of every behavior. Fewer synthetic fixtures in a
public test corpus are not by themselves a removed product capability.

## Separate prototypes, not hidden production features

| Historical work | Current status and required next action |
|---|---|
| Training guards and campaign coordination | Local admission/ledger prototypes; qualified isolated executor and independent watchdog absent. Do not expose local shell as a fallback. |
| Next Useful Observation | Finite synthetic planner previously exercised separately; real response models and empirical decision-quality evaluation absent. Not wired to the core. |
| Geometry Robustness Bench | Separate supervised creation trial, with numerical dependencies and selected privately reviewed application helpers. Not silently included in the zero-dependency core. Review reusable clean interfaces and permissions before packaging it separately. |
| Evidence Desk | Earlier application-envelope-specific prototype; not a universal proof of execution, calibration or quality. |
| Drive / chat-library retrieval | The supervising host supplied authorized copies. This repository does not contain a user's connected account credentials or a full native Drive/chat connector. |
| Automatic semantic contracts and independent invention | Not demonstrated. Transfer Review validates supplied labels, not their truth. |
| Continuously hosted model controller | No model service is started by these commands. Agent integration needs explicit authority, budgets and evaluation. |
| Full language / large-module / archive coverage | Not complete. Large Python structure is bounded at 2 MB, 2,048 complete units and 60,000 AST nodes per unit/shared envelope. Indivisible oversized units and incomplete module context remain explicit; API observations beyond 250 kB remain unavailable. |

These are tracked integration/research requirements, not reasons to publish private
archives or label missing functions as verified. Test against new source families
and strong ordinary-tool comparisons before claiming search or agent superiority.

## Review procedure

Freeze the checkout and target source versions. Keep source copies, workspaces and
outputs separate. Run the original suites, focused regression reproductions,
positive controls, deliberately weakened variants and clean replay. Record failures
without changing deadlines or truth labels. Use source-only synthetic regressions
in public CI. Keep private repository experiments and their detailed results outside
the public repository; only an intentionally reviewed aggregate may be published.

A passing declaration check, hash, test suite or build is not a verified invention,
scientific measurement, market result, license opinion or deployment approval.
