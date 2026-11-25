"""Bounded lexical features. Tokens are declarations, not semantic guarantees."""
from __future__ import annotations
from collections import Counter
import re

VERSION = 'identifier-fields-2-literals'
STOP = frozenset('the a an of for to and or in is are this that with from return returns self def class'.split())

# Small, explicit spelling families. These expand lexical evidence, not behavior.
FAMILIES = (
    {'convert', 'converts', 'converted', 'converting', 'converter', 'conversion'},
    {'wrap', 'wraps', 'wrapped', 'wrapping', 'wrapper'},
    {'int', 'integer', 'integers'}, {'hex', 'hexadecimal'},
    {'percent', 'percentage', 'percentages'}, {'str', 'string', 'strings'},
    {'dict', 'dictionary', 'dictionaries'}, {'bool', 'boolean', 'booleans'},
)
ALIASES = {word: sorted(family) for family in FAMILIES for word in family}

def words(value: str) -> list[str]:
    value = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', value)
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    return [t for t in re.findall(r'[a-z][a-z0-9]{0,63}', value.lower()) if t not in STOP]

def counts(value: str, maximum: int = 256) -> dict[str, int]:
    raw = Counter(words(value))
    # Most frequent terms retained; deterministic order, bounded metadata only.
    chosen = sorted(raw, key=lambda t: (-raw[t], t))[:maximum]
    return {t: min(raw[t], 100) for t in sorted(chosen)}


def compound_parts(token, query_terms):
    """Split an identifier only when the entire token is covered by query words.

    No arbitrary substring votes: ``titlecase`` can cover ``title case``, while
    ``tin`` cannot match ``printing``. The bounded query and 64-character token
    keep this dynamic program small and independent of identifier count.
    """
    candidates = sorted({t for t in query_terms if 3 <= len(t) < len(token)})
    paths = {0: ()}
    for offset in range(len(token)):
        if offset not in paths or len(paths[offset]) == 4:
            continue
        for part in candidates:
            if token.startswith(part, offset):
                end = offset + len(part)
                proposal = paths[offset] + (part,)
                if end not in paths or (len(proposal), proposal) < (len(paths[end]), paths[end]):
                    paths[end] = proposal
    parts = paths.get(len(token), ())
    return parts if len(parts) >= 2 else ()


def expand_query(query_terms, vocabulary, synonyms=None):
    expansion = {t: set((synonyms or {}).get(t, (t,))) | set(ALIASES.get(t, (t,)))
                 for t in query_terms}
    for token in vocabulary:
        for part in compound_parts(token, query_terms):
            expansion[part].add(token)
    return {t: sorted(variants) for t, variants in expansion.items()}
