"""Field-weighted BM25 + reciprocal-rank fusion over private metadata.

No model calls or source execution. Formula-based relevance is NOT confidence,
semantic compatibility, runtime validation or permission to reuse a component.
"""
from __future__ import annotations
import collections, copy, math, re, threading
from functools import lru_cache
from .common import Blocked, canonical, integer, sha, text
from .lexical import counts, words

VERSION = 'field-bm25-rrf-2-role-state'
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


class SearchIndex:
    """Immutable in-memory postings for one metadata snapshot; never holds source bodies."""
    def __init__(self, rows, binding):
        if len(rows) > 100_000: raise Blocked('SEARCH_INDEX_SIZE')
        self.binding = binding; self.rows = copy.deepcopy(rows)
        # Do not silently rerank old observations as though fresh call context exists.
        self.legacy = any(r.get('context_status') == 'REINDEX_REQUIRED' for r in rows)
        self.features = []; self.postings = collections.defaultdict(set); self.groups = collections.defaultdict(list)
        seen = set()
        for index, row in enumerate(self.rows):
            key = occurrence_id(row)
            if key in seen: raise Blocked('DUPLICATE_SEARCH_OCCURRENCE')
            seen.add(key)
            if row.get('runtime_status') != 'NOT_RUN' or row.get('record_kind') not in ('function', 'source_scope'):
                raise Blocked('SEARCH_EVIDENCE_AUTHORITY')
            fields = _fields(row); self.features.append(fields)
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
        expansion = {t: sorted(synonyms.get(t, {t})) for t in qt}
        indexes = set()
        for values in expansion.values():
            for token in values: indexes.update(self.postings.get(token, ()))
        if self.legacy: indexes = set(range(len(self.rows)))
        scored = []; excluded = collections.Counter()
        for i in sorted(indexes):
            row = copy.deepcopy(self.rows[i]); fields = self.features[i]
            if row['test_path'] and not include_tests: excluded['test_occurrences'] += 1; continue
            if 'record_kind' in filters and row['record_kind'] != filters['record_kind']: excluded['kind'] += 1; continue
            if 'container_ids' in filters and row['file_id'] not in filters['container_ids']: excluded['container'] += 1; continue
            apis = set(row['observed_apis'])
            if not set(filters.get('required_apis', ())) <= apis: excluded['required_api_not_observed'] += 1; continue
            if set(filters.get('exclude_apis', ())) & apis: excluded['excluded_api_observed'] += 1; continue
            all_terms = set(row['search_terms']) if self.legacy else set().union(*(set(v) for v in fields.values()))
            matched = [t for t, variants in expansion.items() if set(variants) & all_terms]
            if not matched: continue
            totals = {}; evidence = {}
            for field, weight in WEIGHTS.items():
                terms = {}; score = 0.0
                for qterm, variants in expansion.items():
                    matches = [t for t in variants if t in fields[field]]
                    if not matches: continue
                    terms[qterm] = matches
                    # Synonyms are alternatives: do not add multiple votes for one term.
                    score += max(bm25_term(fields[field][t], sum(fields[field].values()), self.average[field], self.idf[t])
                                 * (1 if t == qterm else .8) for t in matches)
                totals[field] = score * weight
                if terms: evidence[field] = terms
            row.update({'matched_query_terms': matched, 'match_count': len(matched),
                        'all_query_terms_matched': len(matched) == len(qt),
                        'symbol_query_matches': len(set(qt) & set(words(row['name']))),
                        'exact_symbol_match': sorted(set(words(row['name'].split('.')[-1]))) == qt,
                        'text_group_id': group_key(row),
                        'rank_breakdown': {'field_bm25': totals, 'bm25_total': sum(totals.values())},
                        'matched_fields': evidence})
            scored.append(row)
        # Channel rankings contain a content group once, not once per archive copy.
        rankings = []; channel_names = ('lexical', 'symbol', 'call')
        for channel in channel_names:
            best = {}
            for row in scored:
                totals = row['rank_breakdown']['field_bm25']
                value = (row['rank_breakdown']['bm25_total'] if channel == 'lexical' else
                         totals['symbol'] if channel == 'symbol' else totals['observed_api'] + totals['declared_call'])
                if value > 0: best[row['text_group_id']] = max(value, best.get(row['text_group_id'], 0))
            rankings.append(sorted(best, key=lambda g: (-best[g], g)))
        fusion = reciprocal_rank_fusion(rankings)
        channel_ranks = {name: {g: rank for rank, g in enumerate(order, 1)} for name, order in zip(channel_names, rankings)}
        for row in scored:
            group = row['text_group_id']; row['rank_breakdown']['rrf'] = fusion.get(group, 0)
            row['rank_breakdown']['channel_ranks'] = {name: ranks[group] for name, ranks in channel_ranks.items() if group in ranks}
        if self.legacy:
            for row in scored:
                row['symbol_query_matches'] = len(set(qt) & set(legacy_tokens(row['name'])))
                row['exact_symbol_match'] = legacy_tokens(row['name'].split('.')[-1]) == qt
            scored.sort(key=lambda row: (-row['match_count'], -int(row['exact_symbol_match']),
                                        -row['symbol_query_matches'], row['name'], occurrence_id(row)))
        else:
            # Use roles only as a tie-break for class-focused queries. Never infer
            # that __call__ is correct merely from its name or class documentation.
            construction = bool(set(qt) & {'constructor','init','initialize','initialization','configuration','configure','instantiate','new'})
            for row in scored:
                owner = row.get('class_context') or {}
                class_match = bool(set(qt) & set(words(owner.get('name','')))) or 'class_documentation' in row['matched_fields']
                role = row.get('method_role')
                preference = (1 if role == 'CONSTRUCTOR' else -1 if role == 'CALL_OPERATION' else 0) if construction else (1 if role == 'CALL_OPERATION' else -1 if role == 'CONSTRUCTOR' else 0)
                row['rank_breakdown']['role_preference'] = preference if class_match and row.get('implementation_kind') != 'DECLARATION_OR_STUB' else 0
                row['rank_breakdown']['role_policy'] = 'EXPLICIT_CONSTRUCTION' if construction else 'CLASS_BEHAVIOR_TIEBREAK'
            scored.sort(key=lambda row: (-row['match_count'], -int(row['exact_symbol_match']),
                                        -row['symbol_query_matches'], -row['rank_breakdown']['role_preference'], -row['rank_breakdown']['rrf'],
                                        -row['rank_breakdown']['bm25_total'], row['name'], occurrence_id(row)))
        grouped = collections.OrderedDict()
        for row in scored: grouped.setdefault(row['text_group_id'], []).append(row)
        displayed = []
        for group in grouped.values():
            primary = copy.deepcopy(group[0])
            primary['occurrences'] = [{k: r[k] for k in ('record_kind', 'name', 'source_id', 'source_sha256',
                                                        'container_sha256', 'file_id', 'source_url', 'container', 'path',
                                                        'logical_path_hint', 'member_chain', 'format', 'cell_index', 'start_line', 'end_line')}
                                      | ({'definition_id': r['definition_id']} if 'definition_id' in r else {}) for r in group]
            primary['matching_occurrence_count'] = len(group)
            primary['equivalence_scope'] = 'BYTE_IDENTICAL_SEGMENT_AND_DEFINITION_LOCATION_NOT_RUNTIME_OR_RIGHTS_EQUIVALENCE'
            displayed.append(primary)
        warnings = ['REINDEX_REQUIRED_FOR_STRUCTURED_RANKING'] if self.legacy else []
        if set(words(query)) & {'not', 'without', 'never', 'exclude', 'no'}:
            warnings.append('NATURAL_LANGUAGE_NEGATION_NOT_ENFORCED_USE_EXPLICIT_REQUIREMENTS')
        if filters.get('exclude_apis'):
            warnings.append('ABSENT_API_OBSERVATION_DOES_NOT_PROVE_ABSENCE_OF_BEHAVIOR')
        return {'query': query, 'query_terms': qt, 'query_expansion': expansion,
                'filters': filters, 'distinct': distinct, 'total_matching_occurrences': len(scored),
                'distinct_matching_groups': len(grouped), 'full_match_count': sum(r['all_query_terms_matched'] for r in scored),
                'results': (displayed if distinct else scored)[:limit],
                'search_status': 'MATCHES_FOUND' if any(r['all_query_terms_matched'] for r in scored) else
                                 'PARTIAL_TERM_MATCHES_ONLY' if scored else 'NO_METADATA_MATCH',
                'rank_meaning': ('legacy metadata coverage and exact symbols preserved; new call context requires reindexing; BM25 components diagnostic only' if self.legacy else 'metadata term coverage and exact symbols, then field-weighted BM25 / reciprocal rank fusion') + '; NOT confidence, code quality, or verified behavior',
                'ranker_version': 'legacy-coverage-compatible-1' if self.legacy else 'field-bm25-rrf-1',
                'ranking_extension_version': None if self.legacy else VERSION, 'index_binding': self.binding,
                'ranking_mode': 'LEGACY_COMPATIBILITY' if self.legacy else 'STRUCTURED_RETRIEVAL',
                'group_statistics_count': self.group_count, 'exclusion_counts': dict(excluded),
                'warnings': warnings, 'runtime_validation': 'NOT_RUN', 'release_approved': False,
                'match_absence_is_not_capability_absence': True}


_CACHE = collections.OrderedDict()
_CACHE_LOCK = threading.RLock()

def search_report(report, query, limit=20, include_tests=False, distinct=False, filters=None):
    from .corpus import all_rows, SCHEMA
    if not isinstance(report, dict) or report.get('schema') != SCHEMA or report.get('source_bodies_retained') is not False or report.get('upstream_code_executed') is not False:
        raise Blocked('CORPUS_REPORT')
    # Corpus receipts retain timing; semantic cache identity uses only index inputs.
    rows = list(all_rows(report))
    binding = sha(canonical({'version': VERSION, 'rows': rows, 'status': report.get('status'),
                             'manifest': report.get('corpus_manifest_sha256'),
                             'profile': report.get('profile')}))
    receipt_binding = sha(canonical(report))
    with _CACHE_LOCK:
        index = _CACHE.get(binding)
        if index is None:
            index = SearchIndex(rows, binding)
            _CACHE[binding] = index
            while len(_CACHE) > 2: _CACHE.popitem(last=False)
        else: _CACHE.move_to_end(binding)
    result = index.query(query, limit, include_tests, distinct, filters)
    return result | {'coverage_status': report['status'], 'receipt_binding': receipt_binding}
