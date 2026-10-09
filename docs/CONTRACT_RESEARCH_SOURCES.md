# Sources and what they support

- RFC 8785, JSON Canonicalization Scheme, June 2020, Informational: https://www.rfc-editor.org/rfc/rfc8785.html . Read sections 3.1–3.2.4, Appendix A and Appendix B. Used specified key ordering, serialization, Unicode/number limits and published examples. It does not certify FORGE.
- Trail of Bits source, public API, and license: https://github.com/trailofbits/rfc8785.py/tree/010be76943fb879ee3eeed7227e47b6cfb8907ee . Selected two runtime files and LICENSE via authorized GitHub connector. All staged bytes matched Git blob IDs. Native raw access failed DNS, not bypassed. No complete-repository audit or independent license opinion claimed.
- Public API documentation: https://trailofbits.github.io/rfc8785.py/ . Runtime access uses the exported dumps function; internal implementation is preserved but not treated as stable external API.

The number and sorting fixtures are transcribed external examples. Seeded random numeric bit patterns, admission cases, and workbench handoff records are developer-authored data. Keep those categories separate.
