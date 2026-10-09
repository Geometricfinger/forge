"""Bounded lexical features. Tokens are declarations, not semantic guarantees."""
from __future__ import annotations
from collections import Counter
import re

VERSION = 'identifier-fields-1'
STOP = frozenset('the a an of for to and or in is are this that with from return returns self none true false def class'.split())

def words(value: str) -> list[str]:
    value = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', value)
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    return [t for t in re.findall(r'[a-z][a-z0-9]{0,63}', value.lower()) if t not in STOP]

def counts(value: str, maximum: int = 256) -> dict[str, int]:
    raw = Counter(words(value))
    # Most frequent terms retained; deterministic order, bounded metadata only.
    chosen = sorted(raw, key=lambda t: (-raw[t], t))[:maximum]
    return {t: min(raw[t], 100) for t in sorted(chosen)}
