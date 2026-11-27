"""Small explicit query cues; declarations are never behavior guarantees."""
import re

from .lexical import words


def negative_request(query):
    """Recognize explicit negative cues for disclosure, never enforcement.

    Keep the existing token cues, including underscore-separated names. Word
    boundaries on additional prose forms avoid treating ``exceptions`` or
    ``notable`` as prohibitions. This is deliberately not a negation parser.
    """
    return bool(set(words(query)) & {'not', 'without', 'never', 'exclude', 'no'} or
                re.search(r"\b(?:cannot|avoid(?:s|ed|ing)?|exclud(?:e[ds]?|ing)|"
                          r"except|unless|[a-z]+n['\u2019]t)\b", query, re.I))


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
