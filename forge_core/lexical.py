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


def _inflection_bases(token):
    """Possible regular English bases, not a semantic or type-name stemmer."""
    bases = {token}
    if len(token) >= 4 and token.endswith(('ies', 'ied')):
        bases.add(token[:-3] + 'y')
    if len(token) >= 4 and token.endswith('s') and not token.endswith(('ss', 'us', 'is')):
        bases.add(token[:-1])
        if token.endswith(('ches', 'shes', 'ses', 'xes', 'zes')):
            bases.add(token[:-2])
    if token.endswith('ying'):
        bases.add(token[:-4] + 'ie')
    for ending in ('ing', 'ed'):
        if token.endswith(ending) and len(token) - len(ending) >= 2:
            base = token[:-len(ending)]
            bases.update((base, base + 'e'))
            if len(base) >= 4 and base[-1] == base[-2] and base[-1] not in 'aeiou':
                bases.add(base[:-1])
    return bases


def _regular_forms(base):
    forms = {base, base + 's'}
    if base.endswith(('s', 'x', 'z', 'ch', 'sh')):
        forms.add(base + 'es')
    if base.endswith('y') and base[-2] not in 'aeiou':
        forms.update((base[:-1] + 'ies', base[:-1] + 'ied', base + 'ing'))
    elif base.endswith('e'):
        forms.add(base + 'd')
        forms.add(base + 'ing' if base.endswith(('ee', 'ye', 'oe')) else base[:-1] + 'ing')
        if base.endswith('ie'):
            forms.add(base[:-2] + 'ying')
    else:
        doubles = (base[-1] not in 'aeiouwxy' and base[-2] in 'aeiou' and base[-3] not in 'aeiou')
        # A single vowel group gives the ordinary short CVC pattern: plan ->
        # planned, not planed. Multisyllable stress and regional spelling are
        # not inferred; both regular spellings remain possible lexical leads.
        if not doubles or len(re.findall('[aeiou]+', base)) > 1:
            forms.update((base + 'ed', base + 'ing'))
        if doubles:
            forms.update((base + base[-1] + 'ed', base + base[-1] + 'ing'))
    return forms


def inflection_variants(token):
    """Bounded spelling alternatives for regular plurals, past and -ing forms.

    Only one suffix is removed. No derivational endings are stripped: alternate
    and alternative, multiple and multiplication, and iterable and iterator are
    different words. Ambiguous regular forms remain lexical leads, not meaning.
    Numbers, short tokens and arbitrary substrings are not expanded.
    """
    if not re.fullmatch('[a-z]{3,64}', token):
        return {token}
    variants = set()
    for base in _inflection_bases(token):
        if len(base) < 3:
            continue
        forms = _regular_forms(base)
        if token in forms:
            variants.update(forms)
    return {word for word in variants if len(word) <= 64}


def expand_inflections(expansion, vocabulary):
    """Add only forms actually present in this index, without another index copy.

    Opt-in for lexical retrieval. Declaration endpoint/type equivalence continues
    to use expand_query's explicit spelling families, never these alternatives.
    The bounded query drives membership lookups; vocabulary size is not scanned
    per query and no source bodies or external dictionary are needed.
    """
    return {term: sorted(set(variants).union(
                word for variant in variants for word in inflection_variants(variant)
                if word in vocabulary))
            for term, variants in expansion.items()}
