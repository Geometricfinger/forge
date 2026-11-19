"""Bounded, lexical evidence for the order of an explicitly requested conversion."""
import ast
import re

from .lexical import words


# A conversion verb licenses parsing a relation. Its spelling never favors a candidate.
VERB = re.compile(r'\b(?:convert(?:s|ed|ing|er)?|conversion|transform(?:s|ed|ing|ation)?|'
                  r'translat(?:e[ds]?|ing|ion)|map(?:s|ped|ping)?|turn(?:s|ed|ing)?|'
                  r'(?:en|de)cod(?:e[ds]?|ing)|(?:de)?serializ(?:e[ds]?|ing|ation)|'
                  r'(?:un)?pack(?:s|ed|ing)?|render(?:s|ed|ing)?|export(?:s|ed|ing)?|'
                  r'pars(?:e[ds]?|ing)|rewrit(?:e[s]?|ing))\b', re.I)
TO = re.compile(r'\b(?:into|to)\b|->|→', re.I)
FROM = re.compile(r'\bfrom\b', re.I)
FILLER = {'it', 'its', 'back', 'how', 'where', 'what', 'which', 'does', 'do', 'can',
          'function', 'routine', 'takes', 'representation', 'form', 'format'}


def endpoint(text):
    return sorted(set(words(VERB.sub(' ', text))) - FILLER)


def phrase(text):
    """Parse one explicitly directed clause, leaving neutral or two-way prose alone."""
    if re.search(r"\b(?:or|versus|between|both|either|instead|and back|vice versa|not|no|without|never|avoid(?:s|ing)?|exclude(?:s|d)?|except|[a-z]+n['’]t)\b", text, re.I):
        return None
    verb = VERB.search(text)
    if verb:
        before, after = text[:verb.start()], text[verb.end():]
    elif re.search(r'->|→|\bfrom\b.+\b(?:to|into)\b', text, re.I):
        before, after = '', text
    else:
        return None
    destinations = list(TO.finditer(after))
    sources = list(FROM.finditer(after))
    if len(destinations) > 1 or len(sources) > 1:
        return None
    if destinations:
        to = destinations[0]
        if sources and sources[0].start() > to.start():
            return None
        source = after[sources[0].end() if sources else 0:to.start()]
        target = after[to.end():]
        if not source.strip():
            source = before  # e.g. "How are widgets converted to entries?"
    elif sources:
        source = after[sources[0].end():]
        target = after[:sources[0].start()]
    else:
        return None
    # Separators or trailing conditions are outside the endpoints.
    target = re.split(r'[(),:;.!?]|\b(?:with|without|when|while|before|after|using|for|if)\b', target, 1,
                      flags=re.I)[0]
    inputs, outputs = endpoint(source), endpoint(target)
    return (inputs, outputs) if 0 < len(inputs) <= 12 and 0 < len(outputs) <= 6 else None


def type_words(node):
    if isinstance(node, ast.Name):
        return words(node.id)
    if isinstance(node, ast.Attribute):
        return words(node.attr)
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
        return words(node.value)
    return []


def declarations(node):
    """Store only bounded endpoint tokens, never raw source, types or documentation."""
    evidence = []
    name = node.name
    for separator in ('_to_', '_from_'):
        if name.count(separator) == 1:
            left, right = name.split(separator)
            source, target = (left, right) if separator == '_to_' else (right, left)
            evidence.append(('FUNCTION_NAME', endpoint(source), endpoint(target)))
    doc = ast.get_docstring(node, clean=False) or ''
    summary = doc[:384].strip().split('\n\n', 1)[0]
    summary = re.split(r'[.!?](?:\s|$)', summary, 1)[0]
    relation = phrase(summary)
    if relation:
        evidence.append(('DOCSTRING_SUMMARY', *relation))
    parameters = [a for a in [*node.args.posonlyargs, *node.args.args] if a.arg not in ('self', 'cls')]
    if len(parameters) == 1:
        evidence.append(('TYPE_ANNOTATIONS', type_words(parameters[0].annotation), type_words(node.returns)))
    return [{'basis': basis, 'input': source, 'output': target} for basis, source, target in evidence
            if 0 < len(source) <= 12 and 0 < len(target) <= 12]


def match(request, declarations):
    if not request:
        return None
    source, target = map(set, request)
    def agrees(left, right):
        # Require the complete target and a distinguishing output term. A shared
        # word on both sides alone cannot establish an orientation.
        return bool((source - target) & left and target <= right and target & (right - left))

    forward = None
    for declaration in declarations:
        left, right = set(declaration['input']), set(declaration['output'])
        if agrees(right, left):
            return None  # Conflicting declarations cannot earn a preference.
        if agrees(left, right) and forward is None:
            forward = declaration
    return forward
