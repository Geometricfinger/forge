# Research-to-implementation ledger

Research supplied design ideas, not evidence that FORGE works. Formula choices, weights, scope limits and defaults are explicit engineering decisions. No model or embedding dependency was added.

## R1 / The Probabilistic Relevance Framework: BM25 and Beyond

Stephen Robertson and Hugo Zaragoza 2009

Source: https://doi.org/10.1561/1500000019

Access: Publisher abstract and bibliographic record; no paywall bypass and no claim of full monograph access.

Applied: Term-frequency saturation, document-length normalization and field-aware ranking design.

Limit: Our weighted sum of per-field BM25-style scores is not an implementation of exact BM25F and is not certified by the paper.

## R2 / Reciprocal rank fusion outperforms condorcet and individual rank learning methods

Gordon V. Cormack, Charles L. A. Clarke, Stefan Büttcher 2009

Source: https://doi.org/10.1145/1571941.1572114

Access: Primary ACM abstract/bibliographic record; formula cross-checked against the implementation documentation below.

Applied: Combining lexical, symbol and call ranked lists by reciprocal ranks.

Limit: Its published findings are not evidence of FORGE effectiveness. Our small ablation did not improve without richer metadata.

## R3 / Reciprocal rank fusion reference

Elastic 

Source: https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion

Access: Official documentation including formula and rank constant.

Applied: RRF score=sum(1/(60+rank)); one group per channel.

Limit: Elasticsearch itself was not installed or used.

## R4 / CodeSearchNet Challenge: Evaluating the State of Semantic Code Search

Hamel Husain, Ho-Hsiang Wu, Tiferet Gazit, Miltiadis Allamanis, Marc Brockschmidt 2019

Source: https://arxiv.org/abs/1909.09436

Access: Original preprint abstract and metadata.

Applied: Motivates query-level relevance judgments and separation of retrieval evaluation from parser test counts.

Limit: CodeSearchNet was not downloaded, trained on or benchmarked. Our ten target goals are author-reviewed development diagnostics, not full relevance judgments.

## R5 / The Adverse Effects of Code Duplication in Machine Learning Models of Code

Miltiadis Allamanis 2019

Source: https://arxiv.org/abs/1812.06469

Access: Original preprint abstract and metadata.

Applied: Avoid counting repeated code as independent evidence; preserve duplicate origins and group votes.

Limit: The paper studies learning/evaluation leakage. Our retrieval grouping is an engineering application, not its clone detector, and does not establish semantic equivalence.

## R6 / ast — Abstract Syntax Trees

Python Software Foundation 

Source: https://docs.python.org/3.13/library/ast.html

Access: Official reference.

Applied: Function, call, import and scope-location observations.

Limit: AST reading does not execute code or prove runtime binding. Runtime used here is Python 3.13.5; documented grammar versions may differ.

## R7 / FTS5 bm25() reference

SQLite 

Source: https://www.sqlite.org/fts5.html

Access: Official documentation.

Applied: Cross-check term saturation/normalization concepts and conventional k1=1.2, b=0.75.

Limit: This increment uses a Python in-memory index, not an FTS5 deployment; its positive smoothed IDF and weighted per-field combination differ from SQLite exact scoring.
