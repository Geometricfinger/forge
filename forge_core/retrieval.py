"""Field-weighted BM25 + reciprocal-rank fusion over private metadata.

No model calls or source execution. Formula-based relevance is NOT confidence,
semantic compatibility, runtime validation or permission to reuse a component.
"""
from __future__ import annotations
import collections, copy, hashlib, math, re, threading
from functools import lru_cache
from .common import Blocked, canonical, integer, sha, text
from .lexical import counts, expand_inflections, expand_query, words
from .streaming import canonical_chunks
from . import direction, query_intent, return_types

VERSION = 'field-bm25-rrf-7-conversion-destinations'
K1, B, RRF_K = 1.2, 0.75, 60
WEIGHTS = {'symbol': 5.0, 'documentation': 1.0, 'parameters': 0.4,
           'path': 0.3, 'observed_api': 4.0, 'declared_call': 1.3,
           'class_documentation': 0.5, 'state_read': 1.1, 'method_role': 0.2}
SYNONYMS = {
    'align': {'align', 'alignment'},
    'alignment': {'align', 'alignment'},
    'queue': {'queue', 'job', 'jobs'}, 'claims': {'claims', 'claim'},
    'snapshot': {'snapshot', 'backup'}, 'atomic': {'atomic', 'publish', 'publication'},
    'geometry': {'geometry', 'point', 'shape'},
    'validate': {'validate', 'validation', 'verify', 'verification'},
    'hash': {'hash', 'hashing', 'sha256', 'fingerprint'},
    'uncertainty': {'uncertainty', 'unknown', 'ambiguous'},
    'normalize': {'normalize', 'normalization'},
    'save': {'save', 'write', 'persist'}, 'restore': {'restore', 'recover', 'recovery'},
    'neighbors': {'neighbors', 'neighbor', 'nearest'},
}


def occurrence_id(row):
    return row.get('definition_id') or row['source_id'] + ':source'


def group_key(row):
    # Byte-equivalence of a segment is deliberately not equivalence of runtime context.
    value = [row['source_sha256'], row['record_kind'], row['name'],
             row.get('local_start_line', row['start_line']),
             row.get('local_end_line', row['end_line']), row['observed_apis']]
    return 'text_' + sha(canonical(value))


def _canonical_digest(value):
    """Hash the existing canonical bytes without allocating a complete buffer."""
    digest = hashlib.sha256()
    for chunk in canonical_chunks(value):
        digest.update(chunk)
    return digest.hexdigest()


def validate_filters(value):
    if value is None: return {}
    allowed = {'required_apis', 'exclude_apis', 'record_kind', 'container_ids'}
    if not isinstance(value, dict) or set(value) - allowed: raise Blocked('SEARCH_FILTER_FIELDS')
    result = copy.deepcopy(value)
    for key in ('required_apis', 'exclude_apis'):
        vals = result.get(key, [])
        if not isinstance(vals, list) or len(vals) > 12: raise Blocked('SEARCH_API_FILTER')
        if any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', x) or len(x) > 240 for x in vals):
            raise Blocked('SEARCH_API_FILTER')
        if len(set(vals)) != len(vals): raise Blocked('DUPLICATE_SEARCH_FILTER')
    if set(result.get('required_apis', [])) & set(result.get('exclude_apis', [])):
        raise Blocked('CONTRADICTORY_SEARCH_FILTER')
    if 'record_kind' in result and result['record_kind'] not in ('function', 'source_scope'):
        raise Blocked('SEARCH_KIND_FILTER')
    if 'container_ids' in result:
        ids = result['container_ids']
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50: raise Blocked('SEARCH_CONTAINER_FILTER')
        for x in ids: text(x, 200)
    return result


def _fields(row):
    stored = row.get('term_frequencies', {})
    out = {
        'symbol': counts(row['name']),
        'documentation': counts(' '.join(row['documented_terms'])),
        'parameters': counts(' '.join(row['parameters'])),
        'path': counts(row.get('logical_path_hint') or row['path']),
        'observed_api': counts(' '.join(row['observed_apis'])),
        'declared_call': counts(' '.join(row.get('declared_calls', []) + row.get('declared_api_leads', []) + row.get('declared_imports', [])))
    }
    context = row.get('class_context') or {}
    out['class_documentation'] = dict(context.get('documentation_terms', {}))
    out['state_read'] = counts(' '.join(x['name'] for x in row.get('attribute_reads', [])))
    out['method_role'] = counts({'CONSTRUCTOR':'constructor initialize configuration',
                               'CALL_OPERATION':'call operation', 'METHOD':'method',
                               'FUNCTION':'function'}.get(row.get('method_role'), ''))
    for token, n in out['class_documentation'].items():
        if not isinstance(token, str) or not re.fullmatch('[a-z][a-z0-9]{0,63}', token) or type(n) is not int or not 1 <= n <= 100:
            raise Blocked('SEARCH_CLASS_COUNTS')
    if len(out['class_documentation']) > 256: raise Blocked('SEARCH_CLASS_COUNTS')
    for key in ('symbol', 'documentation', 'parameters'):
        if key in stored:
            values = stored[key]
            if not isinstance(values, dict) or len(values) > 256: raise Blocked('SEARCH_TERM_COUNTS')
            for token, n in values.items():
                if not isinstance(token, str) or not re.fullmatch('[a-z][a-z0-9]{0,63}', token) or type(n) is not int or not 1 <= n <= 100:
                    raise Blocked('SEARCH_TERM_COUNTS')
            # Qualified class/parent tokens are a separate observed name feature.
            out[key] = dict(values)
            if key == 'symbol':
                for token, n in counts(row['name']).items(): out[key][token] = max(out[key].get(token, 0), n)
    return out


def bm25_term(tf, length, average, idf, k1=K1, b=B):
    if tf <= 0: return 0.0
    return idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / max(average, 1.0)))


def reciprocal_rank_fusion(rankings, k=RRF_K):
    if type(k) is not int or k < 1: raise Blocked('RRF_K')
    scores = collections.defaultdict(float)
    for ranking in rankings:
        seen = set()
        for position, item in enumerate(ranking, 1):
            if item in seen: raise Blocked('RRF_DUPLICATE_IN_CHANNEL')
            seen.add(item); scores[item] += 1.0 / (k + position)
    return dict(scores)


def query_evidence(terms, matched, basis):
    """Describe overlap for the first returned implementation lead, without reranking."""
    matched = sorted(set(matched))
    unmatched = sorted(set(terms) - set(matched))
    if not matched:
        coverage, action = 'NO_TERM_OVERLAP', 'REFINE_QUERY_OR_REVIEW_SOURCE_SCOPE'
    elif not unmatched:
        coverage, action = 'ALL_QUERY_TERMS', 'INSPECT_SOURCE'
    elif len(matched) == 1:
        coverage, action = 'SINGLE_TERM_OVERLAP', 'INSPECT_SOURCE_OR_REFINE_QUERY'
    else:
        coverage, action = 'MULTIPLE_TERM_OVERLAP', 'INSPECT_SOURCE'
        if len(matched) < len(unmatched):
            action = 'INSPECT_SOURCE_OR_REFINE_QUERY'
    return {'basis': basis, 'term_coverage': coverage, 'matched_terms': matched,
            'matched_term_count': len(matched), 'query_term_count': len(matched) + len(unmatched),
            'unmatched_terms': unmatched, 'next_action': action}


def conversion_target_match(requested, requested_output, row):
    """A single declared destination spelling, without claiming its input.

    All captured conversions must agree on that destination. A full forward or
    reverse relation, ambiguous return metadata, a contradictory return name,
    a stub or uncaptured context cannot earn this weaker, separate cue.
    """
    if (not requested or not requested_output or len(requested[1]) != 1
            or not re.fullmatch(r'[A-Za-z_]\w*', requested_output)
            or row.get('context_status') != 'CAPTURED'
            or row.get('implementation_kind') != 'BODY_PRESENT_NOT_VERIFIED'):
        return None
    destination = direction.normalized(requested[1])
    if destination != direction.normalized(words(requested_output)):
        return None
    if destination & direction.normalized(requested[0]):
        return None
    declarations = row.get('conversion_endpoints', ())
    if not declarations:
        return None
    forward, reverse = direction.evaluate(requested, declarations)
    if forward or reverse:
        return None
    if any(direction.normalized(item['output']) != destination or
           destination & direction.normalized(item['input']) for item in declarations):
        return None
    captured = row.get('return_type_declarations')
    if captured and captured.get('status') != 'CAPTURED':
        return None
    returned = return_types.compare(requested_output, captured, row['implementation_kind'])
    if returned and not returned['name_match']:
        return None
    return {'requested_output': requested_output,
            'declaration': copy.deepcopy(declarations[0]),
            'input_match': 'NOT_ESTABLISHED',
            'evidence': 'CONVERSION_DESTINATION_SPELLING_ONLY_NOT_RETURN_TYPE_OR_BEHAVIOR_PROOF'}


_Score = collections.namedtuple('_Score',
    'index group matched exact symbols lexical symbol call role forward reverse predicate output direct destination')


class SearchIndex:
    """Immutable in-memory postings for one metadata snapshot; never holds source bodies."""
    def __init__(self, rows, binding):
        if len(rows) > 100_000: raise Blocked('SEARCH_INDEX_SIZE')
        self._initialize(copy.deepcopy(rows), binding)

    @classmethod
    def _from_snapshot(cls, rows, binding):
        """Take an already defensive snapshot, after temporary input rows are freed."""
        index = cls.__new__(cls)
        index._initialize(rows, binding)
        return index

    def _initialize(self, rows, binding):
        self.binding = binding; self.rows = rows
        # Do not silently rerank old observations as though fresh call context exists.
        self.legacy = any(r.get('context_status') == 'REINDEX_REQUIRED' for r in rows)
        self.features = []; self.postings = collections.defaultdict(set); self.groups = collections.defaultdict(list)
        self.output_postings = collections.defaultdict(set); self.symbol_tokens = set()
        self.return_postings = collections.defaultdict(set)
        seen = set()
        for index, row in enumerate(self.rows):
            key = occurrence_id(row)
            if key in seen: raise Blocked('DUPLICATE_SEARCH_OCCURRENCE')
            seen.add(key)
            if row.get('runtime_status') != 'NOT_RUN' or row.get('record_kind') not in ('function', 'source_scope'):
                raise Blocked('SEARCH_EVIDENCE_AUTHORITY')
            if row.get('implementation_kind') != 'DECLARATION_OR_STUB':
                for declaration in row.get('conversion_endpoints', ()):
                    for token in declaration['output']:
                        self.output_postings[token].add(index)
                returned = row.get('return_type_declarations') or {}
                if returned.get('status') == 'CAPTURED':
                    for declaration in returned.get('declarations', ()):
                        name = declaration['type'].casefold()
                        self.return_postings[name].add(index)
                        self.return_postings[name.rsplit('.', 1)[-1]].add(index)
            fields = _fields(row); self.features.append(fields)
            self.symbol_tokens.update(fields['symbol'])
            self.groups[group_key(row)].append(index)
            for token in set().union(*(set(x) for x in fields.values())):
                self.postings[token].add(index)
        self.group_count = len(self.groups)
        # Each exact text group contributes at most once to document frequency.
        frequencies = collections.Counter(); lengths = collections.Counter()
        for indexes in self.groups.values():
            merged = {key: {} for key in WEIGHTS}
            for i in indexes:
                for key, values in self.features[i].items():
                    for token, n in values.items(): merged[key][token] = max(merged[key].get(token, 0), n)
            frequencies.update(set().union(*(set(v) for v in merged.values())))
            for key in WEIGHTS: lengths[key] += sum(merged[key].values())
        self.idf = {token: math.log(1 + (self.group_count - n + .5) / (n + .5)) for token, n in frequencies.items()}
        self.average = {key: lengths[key] / max(1, self.group_count) for key in WEIGHTS}

    def query(self, query, limit=20, include_tests=False, distinct=False, filters=None):
        text(query, 200); integer(limit, 1, 100)
        if type(include_tests) is not bool or type(distinct) is not bool: raise Blocked('SEARCH_BOOL')
        filters = validate_filters(filters)
        from .corpus import tokens as legacy_tokens, EXPAND as LEGACY_EXPAND
        qt = legacy_tokens(query) if self.legacy else sorted(set(words(query)))
        if not qt: raise Blocked('QUERY_TERMS_REQUIRED')
        if len(qt) > 32: raise Blocked('QUERY_TERM_LIMIT')
        synonyms = LEGACY_EXPAND if self.legacy else SYNONYMS
        expansion = ({t: sorted(synonyms.get(t, {t})) for t in qt} if self.legacy else
                     expand_query(qt, (), synonyms))
        symbol_expansion = expansion if self.legacy else expand_query(qt, self.symbol_tokens, synonyms)
        if not self.legacy:
            expansion = expand_inflections(expansion, self.postings)
            symbol_expansion = {t: sorted(set(expansion[t]) | set(symbol_expansion[t])) for t in qt}
        indexes = set()
        for values in symbol_expansion.values():
            for token in values: indexes.update(self.postings.get(token, ()))
        if self.legacy: indexes = set(range(len(self.rows)))
        requested = direction.phrase(query) if not self.legacy else None
        requested_output = return_types.request(query) if not self.legacy else None
        if requested_output:
            for token in expand_query([requested_output.casefold()], ())[requested_output.casefold()]:
                indexes.update(self.return_postings.get(token, ()))
        if requested:
            # A simple return annotation can supply an endpoint absent from the
            # ordinary postings; evaluate both endpoints before granting support.
            for token in requested[1]:
                for variant in expansion.get(token, (token,)):
                    indexes.update(self.output_postings.get(variant, ()))
        construction = bool(set(qt) & {'constructor','init','initialize','initialization','configuration','configure','instantiate','new'})
        literal_identifier = bool(re.fullmatch(r'[A-Za-z_]\w*', query.strip()))

        def evaluate(i, detailed=False):
            # Keep only scalar scores for every match. Detailed evidence and
            # independent nested metadata copies are built for displayed rows.
            row = self.rows[i]; fields = self.features[i]
            all_terms = set(row['search_terms']) if self.legacy else set().union(*(set(v) for v in fields.values()))
            matched = [t for t, variants in expansion.items() if set(variants) & all_terms or
                       not self.legacy and set(symbol_expansion[t]) & set(fields['symbol'])]
            declarations = row.get('conversion_endpoints', ())
            returned = return_types.compare(requested_output, row.get('return_type_declarations'), row.get('implementation_kind'))
            support, reverse = (direction.evaluate(requested, declarations)
                                if requested and row.get('implementation_kind') != 'DECLARATION_OR_STUB'
                                else (None, None))
            predicate = (query_intent.predicate_match(query, row['name'], row.get('implementation_kind'), symbol_expansion)
                         if not self.legacy else None)
            destination = (conversion_target_match(requested, requested_output, row)
                           if not self.legacy else None)
            if support:
                declared_terms = set(support['input'] + support['output'])
                endpoint_terms = sorted(t for t, variants in expansion.items() if set(variants) & declared_terms)
                matched = sorted(set(matched).union(endpoint_terms))
            if returned and returned['name_match']:
                return_terms = sorted(set(qt) & set(words(requested_output)))
                matched = sorted(set(matched).union(return_terms))
            if not matched: return None
            totals = {}; evidence = {}
            for field, weight in WEIGHTS.items():
                terms = {}; score = 0.0
                for qterm, variants in (symbol_expansion if field == 'symbol' else expansion).items():
                    matches = [t for t in variants if t in fields[field]]
                    if not matches: continue
                    terms[qterm] = matches
                    # Synonyms are alternatives: do not add multiple votes for one term.
                    score += max(bm25_term(fields[field][t], sum(fields[field].values()), self.average[field], self.idf[t])
                                 * (1 if t == qterm else .8) for t in matches)
                totals[field] = score * weight
                if terms: evidence[field] = terms
            direct = set().union(*(set(terms) for field, terms in evidence.items()
                                  if field not in ('class_documentation', 'path', 'method_role')))
            if support: direct.update(endpoint_terms)
            if returned and returned['name_match']: direct.update(return_terms)
            symbols = (len(set(qt) & set(legacy_tokens(row['name']))) if self.legacy else
                       sum(bool(set(v) & set(words(row['name']))) for v in symbol_expansion.values()))
            exact = (legacy_tokens(row['name'].split('.')[-1]) == qt if self.legacy else
                     row['name'].split('.')[-1].casefold() == query.strip().casefold()
                     if literal_identifier else
                     sorted(set(words(row['name'].split('.')[-1]))) == qt)
            role_preference = 0
            if not self.legacy:
                owner = row.get('class_context') or {}
                class_match = bool(set(qt) & set(words(owner.get('name','')))) or 'class_documentation' in evidence
                role = row.get('method_role')
                preference = (1 if role == 'CONSTRUCTOR' else -1 if role == 'CALL_OPERATION' else 0) if construction else (1 if role == 'CALL_OPERATION' else -1 if role == 'CONSTRUCTOR' else 0)
                role_preference = preference if class_match and row.get('implementation_kind') != 'DECLARATION_OR_STUB' else 0
            group = group_key(row); total = sum(totals.values())
            if not detailed:
                return _Score(i, group, len(matched), exact, symbols, total,
                              totals['symbol'], totals['observed_api'] + totals['declared_call'],
                              role_preference, bool(support), bool(reverse), bool(predicate),
                              0 if returned is None else 1 if returned['name_match'] else -1,
                              len(direct), bool(destination))
            annotated = {k: v for k, v in row.items() if k not in ('conversion_endpoints', 'return_type_declarations')}
            annotated.update({'matched_query_terms': matched, 'match_count': len(matched),
                        'all_query_terms_matched': len(matched) == len(qt),
                        'symbol_query_matches': symbols, 'exact_symbol_match': exact,
                        'text_group_id': group,
                        'rank_breakdown': {'field_bm25': totals, 'bm25_total': total},
                        'matched_fields': evidence})
            breakdown = annotated['rank_breakdown']
            if support:
                breakdown['directional_match'] = support
                evidence['conversion_declaration'] = {t: [t] for t in endpoint_terms}
            if reverse:
                breakdown['directional_conflict'] = reverse
            if predicate:
                breakdown['predicate_match'] = predicate
            if destination:
                breakdown['conversion_target_match'] = destination
            if returned:
                breakdown['declared_output'] = returned
                if returned['name_match']:
                    evidence['return_type_declaration'] = {t: [t] for t in return_terms}
            breakdown['rrf'] = fusion.get(group, 0)
            breakdown['channel_ranks'] = {name: ranks[group] for name, ranks in channel_ranks.items() if group in ranks}
            if not self.legacy:
                breakdown['direct_matched_terms'] = sorted(direct)
                breakdown['role_preference'] = role_preference
                breakdown['role_policy'] = 'EXPLICIT_CONSTRUCTION' if construction else 'CLASS_BEHAVIOR_TIEBREAK'
            return copy.deepcopy(annotated)

        scored = []; excluded = collections.Counter()
        for i in sorted(indexes):
            row = self.rows[i]
            if row['test_path'] and not include_tests: excluded['test_occurrences'] += 1; continue
            if 'record_kind' in filters and row['record_kind'] != filters['record_kind']: excluded['kind'] += 1; continue
            if 'container_ids' in filters and row['file_id'] not in filters['container_ids']: excluded['container'] += 1; continue
            apis = set(row['observed_apis'])
            if not set(filters.get('required_apis', ())) <= apis: excluded['required_api_not_observed'] += 1; continue
            if set(filters.get('exclude_apis', ())) & apis: excluded['excluded_api_observed'] += 1; continue
            score = evaluate(i)
            if score is not None: scored.append(score)
        # Channel rankings contain a content group once, not once per archive copy.
        rankings = []; channel_names = ('lexical', 'symbol', 'call')
        for channel in channel_names:
            best = {}
            for score in scored:
                value = getattr(score, channel)
                if value > 0: best[score.group] = max(value, best.get(score.group, 0))
            rankings.append(sorted(best, key=lambda g: (-best[g], g)))
        fusion = reciprocal_rank_fusion(rankings)
        channel_ranks = {name: {g: rank for rank, g in enumerate(order, 1)} for name, order in zip(channel_names, rankings)}
        if self.legacy:
            scored.sort(key=lambda score: (-score.matched, -int(score.exact), -score.symbols,
                                          self.rows[score.index]['name'], occurrence_id(self.rows[score.index])))
        else:
            # Use roles only as a tie-break for class-focused queries. Never infer
            # that __call__ is correct merely from its name or class documentation.
            has_forward = any(score.forward for score in scored)
            has_output = any(score.output > 0 for score in scored)
            def intent_order(score):
                conflict = has_forward and score.reverse or has_output and score.output < 0
                preference = score.forward or score.predicate or score.output > 0 or score.destination
                return (-int(score.matched == len(qt) and not conflict),
                        -(score.matched + int(preference) - int(conflict)),
                        -int(preference), int(conflict))
            scored.sort(key=lambda score: (-int(literal_identifier and score.exact),
                                          *intent_order(score), -int(score.exact), -score.direct, -score.symbols,
                                          -score.role, -fusion.get(score.group, 0), -score.lexical,
                                          self.rows[score.index]['name'], occurrence_id(self.rows[score.index])))
        distinct_count = len({score.group for score in scored})
        if distinct:
            # Only retain occurrence lists for displayed groups, in the same
            # globally ranked order. Counts above still include every group.
            grouped = {}
            for score in scored:
                if score.group not in grouped:
                    if len(grouped) == limit: continue
                    grouped[score.group] = []
                grouped[score.group].append(score.index)
            displayed = []
            for group in grouped.values():
                primary = evaluate(group[0], detailed=True)
                # Copy each occurrence separately: changing returned provenance
                # must not affect another occurrence, the primary, or the index.
                primary['occurrences'] = [copy.deepcopy(
                    {k: r[k] for k in ('record_kind', 'name', 'source_id', 'source_sha256',
                                      'container_sha256', 'file_id', 'source_url', 'container', 'path',
                                      'logical_path_hint', 'member_chain', 'format', 'cell_index', 'start_line', 'end_line')}
                    | ({'definition_id': r['definition_id']} if 'definition_id' in r else {})) for r in (self.rows[i] for i in group)]
                primary['matching_occurrence_count'] = len(group)
                primary['equivalence_scope'] = 'BYTE_IDENTICAL_SEGMENT_AND_DEFINITION_LOCATION_NOT_RUNTIME_OR_RIGHTS_EQUIVALENCE'
                displayed.append(primary)
        else:
            displayed = [evaluate(score.index, detailed=True) for score in scored[:limit]]
        warnings = ['REINDEX_REQUIRED_FOR_STRUCTURED_RANKING'] if self.legacy else []
        if query_intent.negative_request(query):
            warnings.append('NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS')
        if filters.get('exclude_apis'):
            warnings.append('ABSENT_API_OBSERVATION_DOES_NOT_PROVE_ABSENCE_OF_BEHAVIOR')
        orientation = {}
        if requested:
            orientation['requested_direction'] = {'input': requested[0], 'output': requested[1],
                'matching_occurrences': sum(score.forward for score in scored),
                'reverse_declared_occurrences': sum(score.reverse for score in scored),
                'destination_only_occurrences': sum(score.destination for score in scored)}
        if not self.legacy and query_intent.predicate_request(query):
            orientation['requested_predicate'] = {'matching_occurrences': sum(score.predicate for score in scored),
                'evidence': 'PREDICATE_DECLARATION_NOT_BEHAVIOR_PROOF'}
        if requested_output:
            orientation['requested_output'] = {'type': requested_output,
                'matching_occurrences': sum(score.output > 0 for score in scored),
                'evidence': 'DECLARED_RETURN_NAME_NOT_BEHAVIOR_PROOF', 'runtime_type_relationship': 'NOT_ESTABLISHED'}
        return {'query': query, 'query_terms': qt, 'query_expansion': expansion,
                **({'symbol_query_expansion': symbol_expansion} if symbol_expansion != expansion else {}), **orientation,
                'query_evidence': query_evidence(qt, displayed[0]['matched_query_terms'] if displayed else [], 'TOP_METADATA_RESULT'),
                'filters': filters, 'distinct': distinct, 'total_matching_occurrences': len(scored),
                'distinct_matching_groups': distinct_count, 'full_match_count': sum(score.matched == len(qt) for score in scored),
                'results': displayed,
                'search_status': 'MATCHES_FOUND' if any(score.matched == len(qt) for score in scored) else
                                 'PARTIAL_TERM_MATCHES_ONLY' if scored else 'NO_METADATA_MATCH',
                'rank_meaning': ('legacy metadata coverage and exact symbols preserved; new call context requires reindexing; BM25 components diagnostic only' if self.legacy else 'literal identifier matches first; otherwise complete term coverage first except a reverse conversion or different declared return name when a matching declaration is available; a declared direction, return name, subject-matching predicate, or separately labeled conversion destination can offset one missing term among partial matches, without stacking allowances; destination-only evidence does not establish input matching or a return type; then exact symbols, coverage from direct rather than inherited/path/role fields, symbol matches and field-weighted BM25 / reciprocal rank fusion') + '; NOT confidence, code quality, or verified behavior',
                'ranker_version': 'legacy-coverage-compatible-1' if self.legacy else 'field-bm25-rrf-1',
                'ranking_extension_version': None if self.legacy else VERSION, 'index_binding': self.binding,
                'ranking_mode': 'LEGACY_COMPATIBILITY' if self.legacy else 'STRUCTURED_RETRIEVAL',
                'group_statistics_count': self.group_count, 'exclusion_counts': dict(excluded),
                'warnings': warnings, 'runtime_validation': 'NOT_RUN', 'release_approved': False,
                'match_absence_is_not_capability_absence': True}


_CACHE = collections.OrderedDict()
_CACHE_LOCK = threading.RLock()

class PreparedSearch:
    """An explicit metadata snapshot for several questions about one report.

    Preparation binds and defensively copies index inputs. Later caller changes
    require a new preparation; query results retain the original receipt and do
    not share mutable rows with the snapshot or with each other.
    """
    __slots__ = ('_index', '_coverage_status', '_receipt_binding')

    def __init__(self, index, coverage_status, receipt_binding):
        self._index = index
        self._coverage_status = coverage_status
        self._receipt_binding = receipt_binding

    def query(self, query, limit=20, include_tests=False, distinct=False, filters=None):
        result = self._index.query(query, limit, include_tests, distinct, filters)
        return result | {'coverage_status': self._coverage_status, 'receipt_binding': self._receipt_binding}


def prepare_report(report):
    from .corpus import all_rows, SCHEMA
    if not isinstance(report, dict) or report.get('schema') != SCHEMA or report.get('source_bodies_retained') is not False or report.get('upstream_code_executed') is not False:
        raise Blocked('CORPUS_REPORT')
    # Corpus receipts retain timing; semantic cache identity uses only index inputs.
    rows = list(all_rows(report))
    binding = _canonical_digest({'version': VERSION, 'rows': rows, 'status': report.get('status'),
                                  'manifest': report.get('corpus_manifest_sha256'),
                                  'profile': report.get('profile')})
    receipt_binding = _canonical_digest(report)
    with _CACHE_LOCK:
        index = _CACHE.get(binding)
        if index is None:
            if len(rows) > 100_000: raise Blocked('SEARCH_INDEX_SIZE')
            snapshot = copy.deepcopy(rows)
            del rows
            index = SearchIndex._from_snapshot(snapshot, binding)
            del snapshot
            _CACHE[binding] = index
            while len(_CACHE) > 2: _CACHE.popitem(last=False)
        else:
            del rows
            _CACHE.move_to_end(binding)
    return PreparedSearch(index, report['status'], receipt_binding)


def search_report(report, query, limit=20, include_tests=False, distinct=False, filters=None):
    # Ordinary calls rebind the current mutable report, as before. Batch callers
    # explicitly prepare once and retain only that immutable metadata snapshot.
    return prepare_report(report).query(query, limit, include_tests, distinct, filters)
