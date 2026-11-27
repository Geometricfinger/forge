# Search intent and limits

Forge ranks source leads using declared names, documentation, structural metadata,
and profiled API observations. It does not execute the source or establish that a
candidate satisfies the requested behavior.

## Source scopes and repeated review questions

Observed API calls are joined to a definition's qualified name, definition line,
and end line. The analyzer's nested-scope marker is normalized to the structural
name; repeated functions or classes with the same name remain separate. A
decorator's starting line is not the function's definition line. Legacy
observations without ranges are attached only when the name is unique in the
segment. These are static call observations, not runtime validation.

Library callers asking several questions about unchanged metadata can prepare
one defensive snapshot:

```python
prepared = corpus.prepare_search(report)
first = prepared.query("convert copper to silver", limit=12, distinct=True)
second = prepared.query("has silver", limit=12, distinct=True)
```

Each result retains the original report receipt, coverage and source identities.
Changing the caller's report or a returned result does not change the prepared
snapshot. Prepare again to use changed observations. Ordinary `corpus.search`
still checks the current mutable report on every call. Opportunity Lab prepares
one snapshot per corpus for each batch of missing code-lead queries; its existing
result cache and final corpus-change check remain in place. This avoids repeated
metadata preparation within a batch, not the need to scan changed source.

## Literal and spelling evidence

`None`, `True`, and `False` are retained as searchable Python literals. Existing
indexes that omitted those terms need a fresh scan; no source text is recovered
from an old metadata index.

Search expands a small, explicit set of spelling families: conversion and wrapper
inflections, integer/int, hexadecimal/hex, percentage/percent, string/str,
dictionary/dict, and boolean/bool. The result's `query_expansion` identifies the
spellings used. A declared symbol can also match a complete partition into two
to four query words, each at least three characters: `titlecase` can match
`title case`; `tin` cannot match a substring of `printing`. `symbol_query_expansion`
identifies these extra variants. They apply only to symbol names, not arbitrary
source-body mentions, called identifiers, paths, or documentation. These are lexical
matches and can still be misleading in unfamiliar code.

Fresh structured searches also match bounded regular English word forms, such as
`item/items`, `retry/retries`, `plan/planned/planning`, and `alternate/alternating`.
Only alternatives present in the index vocabulary are added to `query_expansion`;
`matched_fields` shows the actual captured spelling. Each original query word
still contributes at most one coverage vote. A literal identifier query puts the
matching symbol before conceptual declaration cues, including contradictory
documentation attached to that symbol. No additional vocabulary index or
source-body copy is built.

This is deliberately limited to regular plurals, past tense, and `-ing` forms.
Derivational neighbors such as `alternate/alternative`,
`multiple/multiplication`, and `iterable/iterator` remain different words.
Irregular morphology and paraphrases can still be missed, and ambiguous regular
forms can still produce irrelevant leads. Declared type names and conversion
endpoint equivalence keep their existing explicit spelling rules. API filters
still require the recorded API spelling and observation. Legacy reports retain
their previous ranking and expansion behavior.

When coverage, declaration preferences, and exact-symbol priority tie, terms
captured in the definition's own fields take priority over terms found only in
enclosing-class documentation, paths, or generated role labels. The result's
`rank_breakdown.direct_matched_terms` explains this tie-break. Context-only
matches remain searchable and still contribute to reported total coverage.
This does not make direct documentation a verified behavior contract.

## Directed conversions

A positive, unambiguous conversion question can use the declared order in a
`source_to_target` or `target_from_source` name, the first docstring summary, or
simple argument and return annotations. Exact conversion identifier queries keep
their spelling order. Common reStructuredText type roles are stripped while
retaining their declared type words.

Direction requires distinguishing endpoint words. Extra adjectives and
qualifiers remain ordinary query terms; matching an endpoint does not imply that
all of them are satisfied. Results expose a `directional_match` or a
`directional_conflict` in `rank_breakdown`. Conflicting forward and reverse
declarations receive neither preference nor penalty. Stubs receive no preference.

Call/result examples such as `render(value) -> b'example'` are not captured as
source-to-destination declarations. Removing the example arguments could invent
an input relation and interpret literal contents as destination words. Genuine
conversion prose and plain `source -> destination` summaries remain supported.
A fresh scan is required to apply this capture correction to older observations.

Complete term coverage normally comes first. When a forward declaration is
available, an explicitly reverse declaration loses that priority and one lexical
ranking point. A forward declaration can offset one missing term among partial
matches. Other complete-coverage leads retain priority. Reverse leads remain
visible, including when no forward lead exists. This is ranking evidence, not an
automatic dependency choice or a verified conversion contract.

### Destination-only evidence

An explicit positive request for one destination can also use a weaker cue when
the input cannot be matched. For example, a captured `peel_to_bytes` name can
supply the destination spelling for `decode percent escapes into bytes`, while
the requested input remains unestablished. `conversion_target_match` identifies
the original declaration and records `input_match: NOT_ESTABLISHED`; it is not
a declared return type or proof that the function decodes percent escapes.

Every captured conversion declaration must agree on that single destination,
and it must differ from the declared input. A full forward/reverse match,
contradictory or ambiguous captured return metadata, a stub, or missing current
structural context prevents this separate cue. Multiple-word or qualified
destinations such as `models.Bytes` do not receive it; a namespace fragment
cannot stand in for the complete destination spelling. Existing qualified
declared-return matching is separate. Exact identifier and complete-coverage
priority remain intact.

The cue shares the existing one-point allowance for partial matches with
direction, declared return name and predicate evidence; allowances never stack.
It adds no matched input terms. `matched_query_terms`, `full_match_count` and
partial-search status still describe the actual captured overlap. Results can
therefore place a useful destination lead earlier while keeping its missing
requirements visible. The runner recomputes this cue from current corpus
metadata and requires the retained source/body/location binding before using it.

## Declared return names

A request ending in an explicit single type, such as `parse text into a Jewel`,
can use a simple return annotation (including a quoted identifier), a simple
`:rtype:` field, or one positive `:return:` statement such as
``Returns a :class:`types.Jewel` object``. Documentation capture is bounded to
4,096 characters and four declarations. A type mentioned as an input or in a
phrase such as `the length of a Jewel object` does not declare the returned type.

`declared_output` reports the requested name, captured declaration and its basis,
and `name_match`. For an unqualified request, a qualified declaration's final
name can match. An explicitly qualified request requires that qualified spelling.
Type identifiers retain case; only explicit lowercase spelling families normalize.
Alias and subclass relationships remain `NOT_ESTABLISHED`; different names can
still represent compatible runtime types. Nothing is filtered on this basis.

When a matching declared name is available, a differently declared name loses
complete-coverage priority and one lexical ranking point. A matching name can
offset one missing term among partial results, sharing the same single-point
allowance as direction and predicate cues. Candidates without a captured return
name keep their ordinary coverage priority. Conflicting declarations, nullable or
union annotations, `Any`, unsupported annotation expressions, generators, async
callables and stubs receive no return-name preference. Nested generators do not
change an outer function's declared call shape.

This can improve a date-result search by favoring a declared `datetime.date`
over a declared `datetime.datetime`; it does not verify an exact runtime class,
date-only parsing, or agreement between the annotation and implementation.

## Predicates

An explicit `determine whether`, `check if`, `test whether`, `tell if`, or
`predicate` question can favor a function whose declared name begins with `is_`,
`has_`, `can_`, `supports_`, or `contains_` and whose remaining subject matches the
query. The declaration can offset one missing query term among partial results;
it does not defeat ordinary complete coverage. `predicate_match` identifies the
name and matched subject terms. No boolean behavior is inferred from the name.

## Boundaries

Ranked search keeps a defensive metadata snapshot and computes counts over every
matching occurrence. Canonical identity hashes are streamed. Temporary input rows
are released before building the index features. Matching candidates retain
compact scores; field and declaration evidence is assembled only for displayed
rows, each with separate nested copies. A result limit bounds displayed rows; it
does not reduce the corpus-wide statistics or the occurrences retained within a
selected distinct group. Large reports and broad queries still require memory
for the report, index and matching scores. When Python raises `MemoryError`, the
CLI releases the failed command's traceback and writes a fixed
`MEMORY_BUDGET_EXHAUSTED` response without invoking the JSON formatter. Resource
refusals do not claim a complete search.

These cues are shared with the research runner's fused code results. The runner
checks declaration metadata against the retained corpus before using it. Older
external Workbenches without the extension keep their existing behavior, and
metadata without structural context uses compatibility ranking.

Negation, return-value restrictions, side effects, dependency requirements, and
arbitrary natural-language constraints are not enforced. Even a matching declared
return name can be incorrect or omit required behavior. Inspect the
source and use explicit acceptance tests before selecting a component. An exact
symbol is best retrieved with `corpus-symbol`; a lexical lead is not proof of
identity, runtime behavior, safety, or permission to reuse code.
