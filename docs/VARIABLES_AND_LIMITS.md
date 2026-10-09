# Variables reviewed and remaining boundaries

This is a review matrix, not a claim that every program behavior has been considered.

| Area | Checked in this release | Still outside the claim |
|---|---|---|
| Search intent | Construction words, exact symbols, behavior-role tie-breaks, partial terms, class-only terms | General natural-language understanding; semantic constraint proofs |
| Definitions | Direct methods versus nested functions, nested class descriptions, stubs versus nonempty bodies | Complete dynamic class creation, descriptors, decorated runtime substitution |
| State | Load versus Store/Del; augmented attribute update; comparisons; script-only records | Complete alias analysis and runtime data-flow guarantees |
| Helpers | Same-class links; bounded same-file single base; ambiguous, imported, conditional and rebound exclusions | Full MRO, multiple inheritance, external source expansion, whole dependency closure |
| Source identity | Full Git commit, repository-bound URL and ID, envelope and member hashes, strict member lists | Signed provider attestation, proof of ownership or commercial rights |
| Inputs | Types, booleans-as-integers, duplicate/unsafe paths, member modes, archive size/ratio, sensitive/vendor exclusions | General hostile archive containment or a hardened filesystem/network sandbox |
| Reproducibility | Frozen original target contracts, cache reuse, semantic versus receipt identity | Independent held-out outcome study; deployment reproducibility on every host |
| Evidence | Source identities, doc locations, syntax versus observed APIs, separate runtime/rights status | A source comment or profile label being true; hashes authenticating execution |
| Operations | CLI/HTTP agreement, authentication denial, source-preserving scans, finished processes | Native browser policy exception, Mac/Windows qualification, unattended deployment |

Source families in this release are now regression material. Do not present repeated tests as independent samples or turn unsupported-language records into passed coverage. Keep the existing byte, time, depth, entry and authorization limits.

