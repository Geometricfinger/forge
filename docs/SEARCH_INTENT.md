# Search intent and limits

Forge ranks source leads using declared names, documentation, structural metadata,
and profiled API observations. It does not execute the source or establish that a
candidate satisfies the requested behavior.

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

Complete term coverage normally comes first. When a forward declaration is
available, an explicitly reverse declaration loses that priority and one lexical
ranking point. A forward declaration can offset one missing term among partial
matches. Other complete-coverage leads retain priority. Reverse leads remain
visible, including when no forward lead exists. This is ranking evidence, not an
automatic dependency choice or a verified conversion contract.

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
