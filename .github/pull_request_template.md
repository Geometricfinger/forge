## Summary

What does this change and why?

## Checks

- [ ] Tests added or updated for the change
- [ ] `python3 verify.py --out <new folder outside the repo>` passes locally
- [ ] `python3 tools/prepublish_check.py --history` is clean
- [ ] If `hound/`, `addon/` or `loop/` changed: `python3 tools/update_analyzer_manifests.py` was run and the diff is included
- [ ] No new third-party runtime dependencies
- [ ] No secrets, personal data, absolute home paths or real scan output

## Notes for reviewers
