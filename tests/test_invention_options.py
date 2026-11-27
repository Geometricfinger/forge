"""Reviewed-contract route synthesis; fixtures are not real market evidence."""
import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from forge_core import opportunities as op
from forge_core import invention_options as plans
from forge_core.common import Blocked, canonical, sha
from forge_core.opportunity_examples import cite, example_bundle, source


def fixture():
    bundle = example_bundle()
    bundle['mechanisms'] = []
    return bundle


def mechanism(bundle, mid, requires, provides, role='source_component', pattern='identity_loss'):
    row = {
        'id': mid, 'title': mid, 'domain': 'reviewed fixture', 'purpose': 'Review a declared route.',
        'mechanism': 'Apply the supplied input/output contract.', 'role': role,
        'addresses': [pattern], 'requires': requires, 'provides': provides,
        'limits': ['Synthetic contract; implementation and reuse require review.'],
        'evidence': copy.deepcopy(bundle['workflows'][0]['needs'][0]['evidence']),
        'code_queries': [],
    }
    bundle['mechanisms'].append(row)
    return row


def condition(bundle, row, name, state):
    row.setdefault('conditions', []).append({
        'condition': name, 'status': state,
        'evidence': copy.deepcopy(bundle['workflows'][0]['needs'][0]['evidence']),
    })


def hypothesis(bundle):
    return next(h for h in op.evaluate(bundle)['hypotheses'] if h['need']['id'] == 'identity')


def routes(bundle):
    return hypothesis(bundle)['build_options']


class CompositionTests(unittest.TestCase):
    def test_chain_uses_an_upstream_component_from_another_pattern(self):
        bundle = fixture()
        mechanism(bundle, 'parse', ['file'], ['records'], pattern='format_gap')
        mechanism(bundle, 'resolve', ['records'], ['identity'])
        h = hypothesis(bundle)
        self.assertEqual([m['id'] for m in h['mechanisms']], ['resolve'])
        option, = h['build_options']['options']
        self.assertEqual([step['mechanism'] for step in option['steps']], ['parse', 'resolve'])
        self.assertEqual(option['steps'][1]['input_bindings'], [
            {'field': 'records', 'origin': 'mechanism', 'mechanism': 'parse'}])
        self.assertEqual({group['mechanism'] for group in h['code_leads']}, {'parse', 'resolve'})

    def test_all_mandatory_inputs_are_needed(self):
        bundle = fixture()
        mechanism(bundle, 'resolve', ['file', 'missing_signal'], ['identity'])
        result = routes(bundle)
        self.assertEqual(result['options'], [])
        self.assertEqual(result['unresolved']['missing_inputs_without_provider'], ['missing_signal'])
        self.assertFalse(result['coverage']['declared_reachable_without_depth_limit'])

    def test_no_invented_bridge_between_differently_named_fields(self):
        bundle = fixture()
        mechanism(bundle, 'parse', ['file'], ['record'])
        mechanism(bundle, 'resolve', ['records'], ['identity'])
        result = routes(bundle)
        self.assertEqual(result['options'], [])
        self.assertEqual(result['unresolved']['missing_inputs_without_provider'], ['records'])

    def test_a_later_workflow_producer_cannot_supply_earlier_input(self):
        bundle = fixture()
        mechanism(bundle, 'resolve', ['late_signal'], ['identity'])
        workflow = bundle['workflows'][0]
        workflow['steps'].append({'id': 'later', 'action': 'Later signal', 'requires': ['file'],
                                  'produces': ['late_signal'], 'evidence': workflow['steps'][0]['evidence']})
        self.assertEqual(routes(bundle)['options'], [])

    def test_unseeded_cycle_is_explicit_and_produces_no_plan(self):
        bundle = fixture()
        mechanism(bundle, 'a', ['b_value'], ['a_value', 'identity'])
        mechanism(bundle, 'b', ['a_value'], ['b_value'])
        result = routes(bundle)
        self.assertEqual(result['options'], [])
        self.assertEqual(result['unresolved']['cyclic_dependency_groups'], [['a', 'b']])

    def test_a_declared_seed_unlocks_a_cycle_without_extra_step(self):
        bundle = fixture()
        bundle['workflows'][0]['initial_fields'].append('b_value')
        mechanism(bundle, 'a', ['b_value'], ['a_value', 'identity'])
        mechanism(bundle, 'b', ['a_value'], ['b_value'])
        option, = routes(bundle)['options']
        self.assertEqual(option['mechanism_ids'], ['a'])

    def test_self_dependency_does_not_bootstrap(self):
        bundle = fixture()
        mechanism(bundle, 'self', ['identity'], ['identity'])
        result = routes(bundle)
        self.assertEqual(result['unresolved']['cyclic_dependency_groups'], [['self']])
        self.assertEqual(result['options'], [])

    def test_components_are_not_limited_by_six_single_mechanism_cards(self):
        bundle = fixture()
        for i in range(7):
            mechanism(bundle, f'a{i}', ['missing'], ['identity'])
        mechanism(bundle, 'z_source', ['file'], ['records'], pattern='format_gap')
        mechanism(bundle, 'z_resolver', ['records'], ['identity'])
        h = hypothesis(bundle)
        self.assertNotIn('z_source', [m['id'] for m in h['mechanisms']])
        self.assertEqual(h['build_options']['options'][0]['mechanism_ids'], ['z_resolver', 'z_source'])

    def test_independent_component_order_is_collapsed(self):
        bundle = fixture()
        mechanism(bundle, 'left', ['file'], ['left_value'])
        mechanism(bundle, 'right', ['file'], ['right_value'])
        mechanism(bundle, 'join', ['left_value', 'right_value'], ['identity'])
        result = routes(bundle)
        self.assertEqual(len(result['options']), 1)
        self.assertGreater(result['coverage']['equivalent_orders_collapsed'], 0)

    def test_redundant_component_is_not_an_extra_invention(self):
        bundle = fixture()
        mechanism(bundle, 'extra', ['file'], ['intermediate'])
        mechanism(bundle, 'direct', ['file'], ['identity', 'intermediate'])
        option, = routes(bundle)['options']
        self.assertEqual(option['mechanism_ids'], ['direct'])

    def test_distinct_minimal_routes_are_preserved(self):
        bundle = fixture()
        mechanism(bundle, 'left', ['file'], ['left_value'])
        mechanism(bundle, 'left_finish', ['left_value'], ['identity'])
        mechanism(bundle, 'right', ['file'], ['right_value'])
        mechanism(bundle, 'right_finish', ['right_value'], ['identity'])
        self.assertEqual({tuple(p['mechanism_ids']) for p in routes(bundle)['options']},
                         {('left', 'left_finish'), ('right', 'right_finish')})

    def test_mechanism_order_does_not_change_options(self):
        bundle = fixture()
        mechanism(bundle, 'z', ['file'], ['z_value'])
        mechanism(bundle, 'a', ['z_value'], ['identity'])
        mechanism(bundle, 'q', ['file'], ['identity'])
        before = routes(bundle)
        bundle['mechanisms'].reverse()
        self.assertEqual(routes(bundle), before)

    def test_input_bundle_is_unchanged(self):
        bundle = fixture()
        mechanism(bundle, 'direct', ['file'], ['identity'])
        before = copy.deepcopy(bundle)
        routes(bundle)
        self.assertEqual(bundle, before)

    def test_supplied_bridge_is_clearly_new_work(self):
        bundle = fixture()
        mechanism(bundle, 'invent', ['file'], ['identity'], 'new_work')
        option, = routes(bundle)['options']
        self.assertEqual(option['classification'], 'NEW_WORK_REQUIRED')
        self.assertEqual(option['unimplemented_mechanisms'], ['invent'])
        self.assertFalse(option['implementation_tested'])


class EvidenceAndConditionsTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()
        self.row = mechanism(self.bundle, 'direct', ['file'], ['identity'])

    def test_supported_condition_is_a_cited_declaration(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.row, 'offline', 'supported')
        option, = routes(self.bundle)['options']
        self.assertEqual(option['condition_status'], 'DECLARED_SUPPORTED')
        original = self.row['evidence'][0]
        reference, = option['steps'][0]['condition_checks'][0]['evidence']
        self.assertEqual(reference, {**{key: original[key] for key in ('source', 'start', 'end')},
                                     'quote_sha256': sha(original['quote'].encode())})
        self.assertFalse(option['implementation_tested'])

    def test_missing_condition_is_unknown_not_supported(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        option, = routes(self.bundle)['options']
        self.assertEqual(option['condition_status'], 'UNCONFIRMED')
        self.assertEqual(option['unconfirmed_conditions'], [{'mechanism': 'direct', 'condition': 'offline'}])

    def test_explicit_unknown_is_not_converted_into_support(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.row, 'offline', 'unknown')
        self.assertEqual(routes(self.bundle)['options'][0]['condition_status'], 'UNCONFIRMED')

    def test_incompatible_route_is_excluded_and_explained(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.row, 'offline', 'incompatible')
        result = routes(self.bundle)
        self.assertEqual(result['options'], [])
        self.assertEqual(result['unresolved']['incompatible_mechanisms'][0]['mechanism'], 'direct')

    def test_irrelevant_condition_does_not_block_a_route(self):
        condition(self.bundle, self.row, 'unsupported_environment', 'incompatible')
        self.assertEqual(len(routes(self.bundle)['options']), 1)

    def test_one_unknown_upstream_step_keeps_entire_route_unconfirmed(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        self.row['requires'] = ['records']
        condition(self.bundle, self.row, 'offline', 'supported')
        mechanism(self.bundle, 'upstream', ['file'], ['records'])
        option, = routes(self.bundle)['options']
        self.assertEqual(option['condition_status'], 'UNCONFIRMED')
        self.assertEqual(option['unconfirmed_conditions'][0]['mechanism'], 'upstream')

    def test_existing_option_evidence_prevents_new_work_plans(self):
        self.bundle['observations'][0]['kind'] = 'supported'
        self.row['role'] = 'new_work'
        result = routes(self.bundle)
        self.assertEqual(result['status'], 'EXISTING_OPTION_REVIEW_REQUIRED')
        self.assertEqual(result['options'], [])
        self.assertFalse(result['coverage']['search_performed'])

    def test_refutation_prevents_new_work_plans(self):
        self.bundle['observations'][0]['kind'] = 'refutes'
        self.assertEqual(routes(self.bundle)['status'], 'EVIDENCE_REVIEW_REQUIRED')

    def test_conflicting_evidence_prevents_new_work_plans(self):
        self.bundle['sources'][0]['kind'] = 'firsthand'
        other = copy.deepcopy(self.bundle['observations'][0])
        other.update(id='existing', kind='supported')
        self.bundle['observations'].append(other)
        self.assertEqual(routes(self.bundle)['status'], 'EVIDENCE_REVIEW_REQUIRED')

    def test_stale_evidence_prevents_new_work_plans(self):
        self.bundle['sources'][0]['observed_on'] = '2020-01-01'
        self.assertEqual(routes(self.bundle)['status'], 'EVIDENCE_REVIEW_REQUIRED')

    def test_available_output_needs_no_new_build(self):
        self.bundle['workflows'][0]['initial_fields'].append('identity')
        result = routes(self.bundle)
        self.assertEqual(result['status'], 'NO_NEW_WORK_DECLARED_OUTPUT_AVAILABLE')
        self.assertEqual(result['options'], [])

    def test_direct_existing_route_is_checked_before_new_work(self):
        self.row['role'] = 'existing_option'
        mechanism(self.bundle, 'new', ['file'], ['identity'], 'new_work')
        result = routes(self.bundle)
        self.assertEqual(result['next_decision'], 'VERIFY_EXISTING_ROUTE_BEFORE_NEW_WORK')
        self.assertEqual(result['options'][0]['classification'], 'REUSE_EXISTING_OPTION')

    def test_existing_only_chains_are_checked_before_new_work(self):
        for length in (2, 3, 4):
            with self.subTest(length=length):
                bundle = fixture()
                bundle['workflows'][0]['required_conditions'] = ['offline']
                for index in range(length):
                    before = 'file' if index == 0 else f'field{index}'
                    after = 'identity' if index == length - 1 else f'field{index + 1}'
                    row = mechanism(bundle, f'existing{index}', [before], [after], 'existing_option')
                    condition(bundle, row, 'offline', 'supported')
                mechanism(bundle, 'new', ['file'], ['identity'], 'new_work')
                result = routes(bundle)
                self.assertEqual(result['next_decision'], 'VERIFY_EXISTING_ROUTE_BEFORE_NEW_WORK')
                # Classification still describes the multi-mechanism route.
                self.assertEqual(result['options'][0]['classification'], 'COMBINE_DECLARED_MECHANISMS')
                self.assertFalse(any(result['authority'].values()))

    def test_unknown_existing_only_chain_requires_comparison(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        self.row.update(role='existing_option', requires=['records'])
        condition(self.bundle, self.row, 'offline', 'supported')
        mechanism(self.bundle, 'upstream', ['file'], ['records'], 'existing_option')
        result = routes(self.bundle)
        self.assertEqual(result['next_decision'], 'COMPARE_CANDIDATE_ROUTES')
        self.assertEqual(result['options'][0]['condition_status'], 'UNCONFIRMED')

    def test_mixed_existing_route_does_not_claim_existing_only_preference(self):
        for role in ('source_component', 'manual_step', 'new_work', 'unclassified'):
            with self.subTest(role=role):
                bundle = fixture()
                mechanism(bundle, 'upstream', ['file'], ['records'], role)
                mechanism(bundle, 'existing', ['records'], ['identity'], 'existing_option')
                self.assertEqual(routes(bundle)['next_decision'], 'COMPARE_CANDIDATE_ROUTES')

    def test_unclassified_old_mechanism_is_not_labeled_available_code(self):
        self.row.pop('role')
        option, = routes(self.bundle)['options']
        self.assertEqual(option['classification'], 'UNCLASSIFIED_REVIEW_REQUIRED')


class BoundsTests(unittest.TestCase):
    def test_depth_limit_is_explicit_and_does_not_claim_impossibility(self):
        bundle = fixture()
        before = 'file'
        for i in range(5):
            after = 'identity' if i == 4 else f'field{i}'
            mechanism(bundle, f'm{i}', [before], [after])
            before = after
        result = routes(bundle)
        self.assertEqual(result['status'], 'NO_PLAN_FOUND_WITHIN_BOUNDS')
        self.assertTrue(result['coverage']['declared_reachable_without_depth_limit'])
        self.assertTrue(result['coverage']['depth_limited'])
        self.assertEqual(result['coverage']['max_steps'], 4)

    def test_work_budget_reports_omitted_transitions(self):
        bundle = fixture()
        for i in range(10):
            mechanism(bundle, f'start{i}', ['file'], [f'field{i}'])
            mechanism(bundle, f'end{i}', [f'field{i}'], ['identity'])
        with patch.object(plans, 'MAX_STATES', 12):
            result = routes(bundle)
        self.assertEqual(result['coverage']['states_retained'], 12)
        self.assertTrue(result['coverage']['state_budget_exhausted'])
        self.assertFalse(result['coverage']['search_complete_within_depth'])
        self.assertGreater(result['coverage']['transitions_omitted_by_state_budget'], 0)

    def test_result_omissions_are_distinct_from_search_omissions(self):
        bundle = fixture()
        for i in range(12):
            mechanism(bundle, f'route{i:02}', ['file'], ['identity'])
        result = routes(bundle)
        self.assertEqual(len(result['options']), 8)
        self.assertEqual(result['coverage']['minimal_plans_found'], 12)
        self.assertEqual(result['coverage']['options_omitted'], 4)
        self.assertFalse(result['coverage']['state_budget_exhausted'])
        self.assertTrue(result['coverage']['search_complete_within_depth'])

    def test_shortlist_keeps_role_diversity_without_inventing_more_options(self):
        bundle = fixture()
        for i in range(10):
            mechanism(bundle, f'a{i}', ['file'], ['identity'])
        mechanism(bundle, 'z_manual', ['file'], ['identity'], 'manual_step')
        mechanism(bundle, 'z_new', ['file'], ['identity'], 'new_work')
        classes = {option['classification'] for option in routes(bundle)['options']}
        self.assertEqual(classes, {'REUSE_SOURCE_COMPONENT', 'MANUAL_PROCESS', 'NEW_WORK_REQUIRED'})

    def test_small_graph_matches_independent_subset_oracle(self):
        # Enumerate every subset without using the synthesis search or pruning.
        bundle = fixture()
        rows = [('a', ['file'], ['x']), ('b', ['file'], ['y']),
                ('c', ['x', 'y'], ['identity']), ('d', ['x'], ['identity']),
                ('e', ['missing'], ['identity'])]
        for mid, requires, provides in rows:
            mechanism(bundle, mid, requires, provides)
        valid = []
        for size in range(1, 5):
            for group in itertools.combinations(rows, size):
                ids = frozenset(row[0] for row in group)
                known = {'artifact', 'attributes', 'file'}
                for _ in range(len(group)):
                    for _, requires, provides in group:
                        if all(value in known for value in requires):
                            known.update(provides)
                if 'identity' in known and not any(previous < ids for previous in valid):
                    valid.append(ids)
        found = {frozenset(option['mechanism_ids']) for option in routes(bundle)['options']}
        self.assertEqual(found, set(valid))


class SchemaAndPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()
        self.row = mechanism(self.bundle, 'direct', ['file'], ['identity'])

    def test_unknown_role_is_refused(self):
        self.row['role'] = 'proven_invention'
        self.assertRaises(Blocked, op.evaluate, self.bundle)

    def test_duplicate_condition_is_refused(self):
        condition(self.bundle, self.row, 'offline', 'supported')
        condition(self.bundle, self.row, 'offline', 'unknown')
        self.assertRaises(Blocked, op.evaluate, self.bundle)

    def test_unknown_condition_state_is_refused(self):
        condition(self.bundle, self.row, 'offline', 'proven')
        self.assertRaises(Blocked, op.evaluate, self.bundle)

    def test_condition_citation_must_match_the_retained_source(self):
        condition(self.bundle, self.row, 'offline', 'supported')
        self.row['conditions'][0]['evidence'][0]['quote'] = 'Invented statement.'
        self.assertRaises(Blocked, op.evaluate, self.bundle)

    def test_duplicate_required_condition_is_refused(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline', 'offline']
        self.assertRaises(Blocked, op.evaluate, self.bundle)

    def test_tampered_plan_cannot_grant_execution_authority(self):
        report = op.evaluate(self.bundle)
        report['hypotheses'][0]['build_options']['authority']['execution_authorized'] = True
        self.assertRaises(Blocked, op.agent_packet, report)

    def test_tampered_route_is_refused(self):
        report = op.evaluate(self.bundle)
        report['hypotheses'][0]['build_options']['options'][0]['steps'][0]['requires'] = []
        self.assertRaises(Blocked, op.agent_packet, report)

    def test_current_report_requires_catalog_and_options(self):
        report = op.evaluate(self.bundle)
        report.pop('mechanism_catalog')
        self.assertRaises(Blocked, op.agent_packet, report)
        report = op.evaluate(self.bundle)
        report['hypotheses'][0].pop('build_options')
        self.assertRaises(Blocked, op.agent_packet, report)

    def test_old_report_without_options_remains_readable(self):
        report = op.evaluate(self.bundle)
        report['version'] = 'opportunity-1.2-review-leads'
        report.pop('mechanism_catalog')
        report['hypotheses'][0].pop('build_options')
        op.validate_investigation_report(report)

    def test_old_report_cannot_hide_tampered_new_options_by_omitting_agenda(self):
        report = op.evaluate(self.bundle)
        report['version'] = 'opportunity-0.8'
        report['hypotheses'][0].pop('next_investigation')
        report['hypotheses'][0]['build_options']['authority']['execution_authorized'] = True
        self.assertRaises(Blocked, op.agent_packet, report)

    def test_rehashed_store_rejects_changed_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = op.OpportunityStore(SimpleNamespace(home=Path(tmp), guard=lambda: None))
            report = store.run(self.bundle)
            path = store.path(report['id']) / 'record.json'
            value = json.loads(path.read_bytes())
            value['payload']['report']['hypotheses'][0]['build_options']['options'][0]['implementation_tested'] = True
            value['sha256'] = sha(canonical(value['payload']))
            path.write_bytes(canonical(value))
            self.assertRaises(Blocked, store.get, report['id'])

    def test_export_replays_valid_plan_without_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = op.OpportunityStore(SimpleNamespace(home=Path(tmp) / 'home', guard=lambda: None))
            report = store.run(self.bundle)
            out = Path(tmp) / 'export'
            store.export(report['id'], out)
            self.assertEqual(op.verify_export(out)['status'], 'OPPORTUNITY_PACKET_CHECKSUMS_VERIFIED')

    def tamper_record(self, mutate):
        with tempfile.TemporaryDirectory() as tmp:
            store = op.OpportunityStore(SimpleNamespace(home=Path(tmp), guard=lambda: None))
            report = store.run(self.bundle)
            path = store.path(report['id']) / 'record.json'
            value = json.loads(path.read_bytes())
            changed = value['payload']['report']
            mutate(changed)
            for h in changed['hypotheses']:
                h['build_options'] = plans.synthesize(h, changed['mechanism_catalog'])
            value['sha256'] = sha(canonical(value['payload']))
            path.write_bytes(canonical(value))
            self.assertRaises(Blocked, store.get, report['id'])

    def test_recomputed_catalog_cannot_disagree_with_retained_input(self):
        self.tamper_record(lambda r: r['mechanism_catalog'][0].update(role='existing_option'))

    def test_recomputed_conditions_cannot_disagree_with_retained_input(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        self.tamper_record(lambda r: r['hypotheses'][0]['workflow_context'].update(required_conditions=[]))

    def test_recomputed_available_fields_cannot_disagree_with_retained_input(self):
        self.tamper_record(lambda r: r['hypotheses'][0]['workflow_readiness']['available_before'].append('identity'))

    def test_unencodable_portable_citation_is_a_controlled_refusal(self):
        report = op.evaluate(self.bundle)
        report['mechanism_catalog'][0]['evidence'] = [
            {'source': 'operator', 'start': 0, 'end': 1, 'quote': '\ud800'}]
        self.assertRaises(Blocked, op.agent_packet, report)

    def test_valid_non_ascii_citation_retains_utf8_digest(self):
        note = source('unicode', 'Réviser le document.', kind='synthetic')
        self.bundle['sources'].append(note)
        self.row['evidence'] = [cite(note)]
        report = op.evaluate(self.bundle)
        reference, = report['hypotheses'][0]['build_options']['options'][0]['steps'][0]['evidence']
        self.assertEqual(reference['quote_sha256'], sha(note['text'].encode('utf-8')))
        op.agent_packet(report)

    def test_oversized_record_is_refused_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = op.OpportunityStore(SimpleNamespace(home=Path(tmp), guard=lambda: None))
            with patch.object(op, 'MAX_RECORD_BYTES', 1):
                with self.assertRaisesRegex(Blocked, 'OPPORTUNITY_RECORD_BOUNDS'):
                    store.run(self.bundle)
            self.assertFalse(store.root.exists())

    def test_record_at_exact_byte_boundary_can_be_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = op.OpportunityStore(SimpleNamespace(home=Path(tmp) / 'a', guard=lambda: None))
            report = original.run(self.bundle)
            size = (original.path(report['id']) / 'record.json').stat().st_size
            bounded = op.OpportunityStore(SimpleNamespace(home=Path(tmp) / 'b', guard=lambda: None))
            with patch.object(op, 'MAX_RECORD_BYTES', size):
                self.assertEqual(bounded.run(self.bundle), report)
                self.assertEqual(bounded.get(report['id']), report)


class PlanComparisonTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()
        self.parser = mechanism(self.bundle, 'parse', ['file'], ['records'], pattern='format_gap')
        self.resolver = mechanism(self.bundle, 'resolve', ['records'], ['identity'])

    def compare(self, before, after):
        from forge_core.opportunity_compare import compare
        result = compare({'bundle': before, 'report': op.evaluate(before)},
                         {'bundle': after, 'report': op.evaluate(after)})
        return next(h for h in result['hypotheses'] if h['title'].endswith('identity handoff review'))

    def test_upstream_cross_pattern_evidence_is_compared(self):
        before = copy.deepcopy(self.bundle)
        note = source('changed', 'A different parser contract needs inspection.', kind='synthetic')
        self.bundle['sources'].append(note)
        self.parser['evidence'] = [cite(note)]
        row = self.compare(before, self.bundle)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertTrue(any(c['role'] == 'mechanism' and c['owner'] == 'parse'
                            and c['status'] == 'NEW_CITATION' for c in row['citations']))

    def test_condition_evidence_has_its_own_citation_role(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.parser, 'offline', 'supported')
        before = copy.deepcopy(self.bundle)
        note = source('changed', 'Offline behavior needs inspection.', kind='synthetic')
        self.bundle['sources'].append(note)
        self.parser['conditions'][0]['evidence'] = [cite(note)]
        row = self.compare(before, self.bundle)
        self.assertTrue(any(c['role'] == 'mechanism_condition' and c['owner'] == 'parse:offline'
                            and c['status'] == 'NEW_CITATION' for c in row['citations']))

    def test_condition_state_change_requires_plan_review(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.parser, 'offline', 'supported')
        before = copy.deepcopy(self.bundle)
        self.parser['conditions'][0]['status'] = 'unknown'
        self.assertIn('BUILD_OPTIONS_CHANGED', self.compare(before, self.bundle)['reasons'])

    def test_same_condition_quote_moving_is_not_a_changed_route(self):
        note = source('condition', 'Declared offline operation.', kind='synthetic')
        self.bundle['sources'].append(note)
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        self.parser['conditions'] = [{'condition': 'offline', 'status': 'supported',
                                      'evidence': [cite(note, 'offline operation')]}]
        before = copy.deepcopy(self.bundle)
        note['text'] = 'Additional context. Declared offline operation.'
        self.parser['conditions'][0]['evidence'] = [cite(note, 'offline operation')]
        row = self.compare(before, self.bundle)
        self.assertEqual(row['status'], 'CITATIONS_RETAINED')
        self.assertIn('MOVED', {citation['status'] for citation in row['citations']})

    def test_changed_workflow_requirement_is_not_hidden(self):
        before = copy.deepcopy(self.bundle)
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        row = self.compare(before, self.bundle)
        self.assertIn('BUILD_OPTIONS_CHANGED', row['reasons'])
        self.assertIn('DECLARED_WORKFLOW_OR_INTERPRETATION_CHANGED', row['reasons'])

    def test_blocked_branch_condition_evidence_is_also_compared(self):
        self.bundle['workflows'][0]['required_conditions'] = ['offline']
        condition(self.bundle, self.parser, 'offline', 'incompatible')
        before = copy.deepcopy(self.bundle)
        note = source('changed', 'The online-only parser has a revised limitation.', kind='synthetic')
        self.bundle['sources'].append(note)
        self.parser['conditions'][0]['evidence'] = [cite(note)]
        row = self.compare(before, self.bundle)
        self.assertEqual(row['status'], 'REVIEW_REQUIRED')
        self.assertTrue(any(c['owner'] == 'parse:offline' and c['status'] == 'NEW_CITATION'
                            for c in row['citations']))


if __name__ == '__main__':
    unittest.main()
