# Decisions and rejected shortcuts

- **Keep baseline behavior:** existing deterministic JSON is valid for its original Python-specific contract. Failure on newly imposed JCS examples is a requirements mismatch, not proof that historical evidence was corrupt.
- **Adopt rather than rewrite:** evaluate a maintained, small public implementation, retain its unmodified source and Apache-2.0 license, and add an application-specific wrapper.
- **Published expectations:** RFC 8785 (Informational, not Standards Track) supplies 26 Appendix B cases and two structured examples. The wrapper adds narrower input constraints. Do not confuse component-domain results with wrapper-domain acceptance.
- **Cross-runtime reference:** V8 formats primitives; small authored JavaScript glue performs recursive key ordering. This is stronger than Python-only self-agreement, but not an independent review team or complete semantic proof.
- **Strict input boundary:** raw UTF-8 object bytes only, preventing already-decoded duplicate-key information from disappearing. Oversized/deep inputs, nonfinite values, invalid Unicode, out-of-domain integers and underflow are explicit errors.
- **No automatic migration:** the new scheme identifier is distinct. Legacy hashes and evidence remain unchanged. A later interoperability scheme revision needs a new identifier and tests.
- **No silent readiness:** missing Node, failed vectors, missing selected source or incomplete admission tests prevents integration-review readiness. More test counts alone do not supply demand evidence.
- **Execution distinction:** source inspection does not execute its content. The later trial executes only the fixed reviewed component and authored reference code in the local trusted environment. This is not a hostile-code sandbox.
- **Broader roadmap:** semantic need–solution mining, independently maintained retrieval studies, real customer workflow trials, generic adapter synthesis, isolated execution, and hosted/native qualification remain separate tasks. This release implements the first concrete requirement-to-integration step from section 7.
