# References consulted for this increment

- Python 3.13 AST reference: https://docs.python.org/3.13/library/ast.html — ClassDef, source positions, Attribute contexts, AugAssign and comparisons. Used to choose syntax observations, not to assert runtime semantics.
- Python 3.13 `super` documentation: https://docs.python.org/3.13/library/functions.html#super — method lookup follows runtime method-resolution order. The implemented helper is explicitly a constrained source lead, not a replacement MRO engine.
- GitHub repository contents REST documentation: https://docs.github.com/en/rest/repos/contents — revision-addressed source and blob metadata. Actual source acquisition used the connected read tools; the new envelope checker performs no requests.
- Tenacity API documentation: https://tenacity.readthedocs.io/en/latest/api.html — useful distinctions between wait policies and stopping predicates. Actual retrieval evidence is bound to the exact inspected source, not whatever moving documentation later says.

The BM25/RRF and duplicate-aware retrieval research cited by release 0.6 remains background for the preserved ranking core. This release does not claim a new academic algorithm or reproduction of an independent published benchmark. The parameters and role preference are disclosed engineering heuristics assessed on the recorded tasks.
