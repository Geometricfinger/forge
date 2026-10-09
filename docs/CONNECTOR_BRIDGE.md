# Existing-agent connector bridge

The native HTTPS adapter and a host-provided GitHub connector are separate access mechanisms. This bridge allows an authorized host agent to carry normalized responses between them without copying connector credentials into FORGE.

1. Create a mission using the preset and public brief.
2. Export a request with `python3 forge.py --home /HOME packet MISSION --out /NEW/request.json`.
3. The packet specifies one GET-only operation and a bounded response size. Execute the matching supported connector read/search. Do not assume a generic fetch wrapper supports every URL; use the proper repository-search action when required.
4. Normalize only actually returned facts into `{"status":200,"body":{...},"headers":{},"provenance":"operator_connector"}`. Missing search completeness must be reported unknown/incomplete. A selected subset of a tree must set `truncated:true`. Never invent `total_count`, response headers, public visibility, a license, or source bytes.
5. Submit with `python3 forge.py --home /HOME accept --packet /request.json --response /response.json`.
6. Review status, export new packets, and stop on budget, hold, cancellation, unsupported permissions, or completed scope.

The acquisition ledger persists response digests and normalized source evidence, not raw source bodies. Temporary files created by a host agent for connector responses remain that host's responsibility. They may contain code and private claim tokens; do not redistribute them with reports.

Claim packets expire after five minutes. A late or wrong claim cannot complete a newer assignment. A repeated identical successful response is idempotent; a different replay is rejected. An agent under the same local OS user can still alter state or forge a connector response: this is not a signed independent acquisition service.

Live test: a public repository query returned SymDex and two other results. One repository's metadata and exact commit were followed. Three observed tree entries were provided as a truncated subset, one source selected, and its fetched UTF-8 bytes reconstructed exactly; the computed Git blob hash matched. This supervisory handoff completed five internal acquisition stages and a real Hound scan, while retaining `COMPLETED_WITH_GAPS`. It does not claim a full repository or native direct-network run.
