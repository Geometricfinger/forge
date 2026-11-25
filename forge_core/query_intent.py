"""Small explicit query cues; declarations are never behavior guarantees."""
import re

from .lexical import words


def predicate_request(query):
    return bool(re.search(r'\b(?:determine|check|test|tell)\s+(?:whether|if)\b|\bpredicate\b', query, re.I))


def predicate_match(query, name, implementation_kind, expansion):
    if not predicate_request(query) or implementation_kind == 'DECLARATION_OR_STUB':
        return None
    leaf = name.rsplit('.', 1)[-1]
    found = re.match(r'^(?:is|has|can|supports|contains)_(.+)$', leaf)
    if not found:
        return None
    declared = set(words(found[1]))
    subjects = sorted(t for t, variants in expansion.items() if declared & set(variants))
    if not subjects:
        return None
    return {'basis': 'FUNCTION_NAME', 'name': leaf, 'matched_subject_terms': subjects,
            'evidence': 'PREDICATE_DECLARATION_NOT_BEHAVIOR_PROOF'}
