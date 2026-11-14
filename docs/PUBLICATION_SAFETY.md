# Publishing FORGE evidence

Public proof of work must contain only material intended for public release.
Keep private source, project reviews, credentials, machine configuration and
combined private delivery packages outside the repository and public artifacts.
Use synthetic inputs and already-public, appropriately licensed fixtures for CI.

## Two separate checks

`tools/prepublish_check.py --history` checks the current checkout and reachable
Git history before qualification. It scans the checker and allowlist too.
Exact fixture exceptions apply only to their approved spans; they do not exempt
the rest of a file or line. Historical public-identity exceptions apply only to
the specific historical email match and never authorize current source or
generated evidence to contain that identity.

Generated test results need another check after generation. CI binds the
evidence to its commit, then runs `tools/prepare_public_evidence.py`. The tool
checks the exact selected file set and integrity records, scans the bytes and
bounded decoded archive content, and stages an unchanged copy in a new external
directory only when all checks pass. The upload step reads that directory.
Qualification and demo stdout/stderr are captured in evidence files rather than
streamed to the public console. Only after staging succeeds does the job summary
show fixed publication text and GitHub job metadata; it does not echo arbitrary
artifact fields. A blocked publication produces a generic withheld notice.
Binding diagnostics stay outside the public console and evidence directory,
because an I/O error could otherwise reveal an unreviewed filename.

The excluded `qualification/home` tree is the existing private runtime exclusion.
Every other selected file, including hidden files, must pass. A different folder
name is not an exclusion. Source fixture exceptions require exact public source
bytes and exact approved literals. The optional CI-runner-path exception is
limited to the generic GitHub runner home; it is not permission to publish
personal home directories.

The publication check does not import or execute artifact source, contact a
credential provider, or decide whether a credential is live. It reports safe
categories and opaque identifiers rather than printing matched values or
sensitive filenames. Malformed, unreadable, unsupported or over-budget content
blocks the publication check rather than becoming an unscanned success.

The gate admits UTF-8 and BOM-marked UTF-16 text, ZIP/gzip containers and SQLite
data. Other opaque binary formats are refused. Limits include 32 MB per file,
256 MB of selected input, 512 MB of cumulative scan data, four nested levels and
four million JSON nodes. These are publication budgets, not a promise that every
possible secret encoding is recognized.

## Failed tests and failed publication checks

A failed qualification can still have useful, intact evidence. Such evidence may
be uploaded if source publication, evidence binding and the final publication
check all pass. Its recorded test failure remains a failure.

A failed or skipped source/publication check never authorizes an evidence upload.
The workflow does not fall back to uploading the original working directory.
Do not weaken the gate or suppress a finding to make an artifact appear.

## Private terms

Repository-specific private names and locations can be supplied through an
external `--denylist` file. Never commit that file or print its contents. CI uses
the optional `FORGE_DENYLIST` repository secret in the source and evidence steps,
keeps the temporary file outside both evidence directories and removes it on
exit. Fork pull requests may lack this secret; their checks still use built-in
patterns, and the workflow does not claim that a private denylist was applied.

## Review before publishing

Check source changes, commit messages, pull-request text, attached files, job logs
and downloadable artifacts. Include only the minimum material needed to support
the claim. A private report shared with its owner is not automatically suitable
for a public repository. A checksum proves byte consistency; it does not make
private content safe to publish.

Credential and personal-data pattern checks reduce accidental disclosure. They
cannot establish that arbitrary bytes contain no secret, understand every
encoding or replace a review of what was selected. Keep operational credentials
out of the qualification environment; retain read-only job permissions and
disabled persisted checkout credentials.

If an actual credential is exposed, stop publication and notify its owner
privately. Revocation or rotation is the first containment step; deleting a
current file does not revoke a credential or erase earlier public copies.
Follow [SECURITY.md](../SECURITY.md) for private reporting. Do not include the
credential in a public issue, patch description or screenshot.

Git author attribution and older public material may remain in history. A
history-only exception records that existing material; it does not erase it.
History rewriting is a separate coordinated action because it changes commit
identities and can invalidate published evidence bindings.
