"""Optional structural growth retains bounded original results on refusal.

Small policy-scaled tests exercise fallback branches independently of wall-time.
Real fixed-worker capacity measurements are separate from these control tests.
All inspected programs are synthetic text and are never executed.
"""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from forge_core import corpus, mixed_intake, structural_units
from forge_core.common import canonical, sha
from forge_core.corpus_checkpoint import validate_result


def padded(code):
    raw=code.encode()
    return raw+b'#'+b'.'*max(0,250_001-len(raw)-2)+b'\n'


def functions(count,prefix='item'):
    return ''.join(f'def {prefix}_{i}():\n    return {i}\n' for i in range(count))


def notebook(*sources):
    return canonical({'metadata':{'language_info':{'name':'python'}},
                      'cells':[{'cell_type':'code','source':s.decode()} for s in sources]})


@contextmanager
def scaled_policy(clock=1000.0):
    with patch.object(structural_units,'LEGACY_MAX_UNITS',2), \
         patch.object(structural_units,'MAX_UNITS',4), \
         patch.object(mixed_intake.time,'process_time',return_value=clock):
        yield


def old_result(data,name='module.py'):
    return mixed_intake._result(data,mixed_intake._read_container(data,name,None,2))


class StructuralCapacityTests(unittest.TestCase):
    def test_real_unit_boundary_keeps_an_exact_unvalidated_tail(self):
        for count in (4095,4096,4097):
            with self.subTest(count=count):
                code=functions(count)
                meta=structural_units.plan(code,60_000)['metadata']
                self.assertEqual(meta['units_analyzed'],min(count,4096))
                self.assertEqual(meta['units_omitted'],max(0,count-4096))
                self.assertEqual(meta['module_context_complete'],count<=4096)
                self.assertEqual(meta['syntax_validation_complete'],count<=4096)
                raw=code.encode()
                self.assertEqual(b''.join(raw[u['byte_start']:u['byte_end']] for u in meta['units']),raw)
                for unit in meta['units']:
                    self.assertEqual(sha(raw[unit['byte_start']:unit['byte_end']]),unit['sha256'])
                if count>4096:
                    self.assertEqual(meta['units'][-1]['status'],'STRUCTURAL_UNIT_COUNT_LIMIT')
                    self.assertEqual(meta['units'][-1]['unit_count'],1)

    def test_ordinary_admission_is_not_capped_by_partition_units(self):
        code=functions(4097)
        self.assertLess(len(code.encode()),250_000)
        result=mixed_intake.inspect_container(code.encode(),'module.py')
        self.assertEqual(len(result['segments'][0]['definitions']),4097)
        self.assertNotIn('structural_partition',result['segments'][0])

    def test_growth_restores_complete_context_and_original_windows(self):
        data=padded('import hashlib as digest\n'
                    'def first(value):\n    return digest.sha256(value)\n'
                    'def second(value):\n    return first(value)\n'
                    'def third(value):\n    return second(value)\n')
        with scaled_policy():
            result=mixed_intake.inspect_container(data,'module.py')
        segment=result['segments'][0]
        self.assertEqual([d['name'] for d in segment['definitions']],['first','second','third'])
        partition=segment['structural_partition']
        self.assertTrue(partition['module_context_complete'])
        self.assertEqual(partition['limits']['max_units'],4)
        self.assertNotIn('capacity_fallback',partition)
        first=segment['definitions'][0]
        self.assertIn('hashlib.sha256',{r['name'] for r in first['structure']['declared_api_leads']})
        for definition in segment['definitions']:
            window=definition['source_window']
            self.assertEqual(sha(data[window['byte_start']:window['byte_end']]),window['sha256'])
        self.assertIn('SOURCE_SIZE_LIMIT',{g['status'] for g in result['gaps']})

    def test_result_budget_retains_the_original_incomplete_prefix(self):
        data=padded(functions(4))
        with scaled_policy():
            before=old_result(data)
            expanded=mixed_intake.inspect_container(data,'module.py')
            budget=(len(canonical(before))+len(canonical(expanded)))//2
            with patch.object(mixed_intake,'EXTENDED_RESULT_BYTES',budget):
                actual=mixed_intake.inspect_container(data,'module.py')
        expected=before['segments'][0]
        segment=actual['segments'][0]
        self.assertEqual(segment['definitions'],expected['definitions'])
        self.assertEqual(segment['module_structure'],expected['module_structure'])
        self.assertEqual(actual['inventory'],before['inventory'])
        partition=segment['structural_partition']
        self.assertFalse(partition['module_context_complete'])
        self.assertFalse(partition['syntax_validation_complete'])
        self.assertEqual(partition['capacity_fallback']['reason'],'RESULT_BUDGET')
        self.assertEqual(partition['capacity_fallback']['scope'],'WHOLE_CONTAINER')

    def test_whole_container_budget_counts_multiple_segments(self):
        data=notebook(padded(functions(4,'left')),padded(functions(4,'right')))
        with scaled_policy():
            before=old_result(data,'input.ipynb')
            expanded=mixed_intake.inspect_container(data,'input.ipynb')
            budget=(len(canonical(before))+len(canonical(expanded)))//2
            self.assertTrue(all(len(canonical(s))<budget for s in expanded['segments']))
            with patch.object(mixed_intake,'EXTENDED_RESULT_BYTES',budget):
                actual=mixed_intake.inspect_container(data,'input.ipynb')
        self.assertEqual([len(s['definitions']) for s in actual['segments']],[2,2])
        self.assertTrue(all(s['structural_partition']['capacity_fallback']['reason']=='RESULT_BUDGET'
                            for s in actual['segments']))

    def test_compact_receipt_never_overflows_a_fitting_old_result(self):
        data=padded(functions(4))
        with scaled_policy():
            before=old_result(data)
            hard_limit=len(canonical(before))
            with patch.object(mixed_intake,'MAX_RESULT_BYTES',hard_limit), \
                 patch.object(mixed_intake,'EXTENDED_RESULT_BYTES',hard_limit-1):
                actual=mixed_intake.inspect_container(data,'module.py')
        self.assertLessEqual(len(canonical(actual)),hard_limit)
        partition=actual['segments'][0]['structural_partition']
        self.assertNotIn('capacity_fallback',partition)
        self.assertEqual(partition['policy'],'unit-capacity-v2-bytes')
        self.assertEqual(partition['limits']['max_units'],2)
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])

    def test_optional_cpu_exhaustion_returns_prefix_without_an_ast_gap(self):
        data=padded(functions(4))
        now=[100.0]
        original=mixed_intake.Reader.visit
        def advance_clock(reader,*args,**kwargs):
            if reader.unit_limit==4:now[0]=105.0
            result=original(reader,*args,**kwargs)
            if reader.unit_limit==2:now[0]=101.0
            return result
        with scaled_policy():
            before=old_result(data)
            with patch.object(mixed_intake.time,'process_time',side_effect=lambda:now[0]), \
                 patch.object(mixed_intake.Reader,'visit',advance_clock):
                actual=mixed_intake.inspect_container(data,'module.py')
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])
        self.assertEqual(actual['segments'][0]['structural_partition']['capacity_fallback']['reason'],'CPU_BUDGET')
        self.assertNotIn('AST_LIMIT',{g['status'] for g in actual['gaps']})

    def test_late_baseline_avoids_all_optional_serialization(self):
        data=padded(functions(4))
        now=[100.0]
        original=mixed_intake.Reader.visit
        def expensive_baseline(reader,*args,**kwargs):
            result=original(reader,*args,**kwargs)
            now[0]=108.0
            return result
        with scaled_policy():
            before=old_result(data)
            with patch.object(mixed_intake.time,'process_time',side_effect=lambda:now[0]), \
                 patch.object(mixed_intake.Reader,'visit',expensive_baseline), \
                 patch.object(mixed_intake,'_fits_result',side_effect=AssertionError('Late byte traversal')):
                actual=mixed_intake.inspect_container(data,'module.py')
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])
        self.assertEqual(actual['segments'][0]['structural_partition']['policy'],'unit-capacity-v2-cpu')
        self.assertLessEqual(len(canonical(actual)),len(canonical(before)))

    def test_byte_accounting_exhaustion_does_not_start_another_traversal(self):
        data=padded(functions(4))
        now=[100.0]
        def expensive_counter(result,limit,check_budget=None):
            now[0]=108.0
            check_budget()
            self.fail('Counter failed to check its CPU budget')
        with scaled_policy():
            before=old_result(data)
            with patch.object(mixed_intake.time,'process_time',side_effect=lambda:now[0]), \
                 patch.object(mixed_intake,'_fits_result',side_effect=expensive_counter) as counter:
                actual=mixed_intake.inspect_container(data,'module.py')
            self.assertEqual(counter.call_count,1)
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])
        self.assertEqual(actual['segments'][0]['structural_partition']['policy'],'unit-capacity-v2-cpu')

    def test_cpu_accounting_is_relative_to_each_call(self):
        with scaled_policy(clock=50_000.0):
            actual=mixed_intake.inspect_container(padded(functions(4)),'module.py')
        self.assertEqual(len(actual['segments'][0]['definitions']),4)
        self.assertNotIn('capacity_fallback',actual['segments'][0]['structural_partition'])

    def test_extension_memory_failure_keeps_original_metadata(self):
        data=padded(functions(4))
        original=mixed_intake.Reader.visit
        def fail_extended(reader,*args,**kwargs):
            if reader.unit_limit==4:raise MemoryError()
            return original(reader,*args,**kwargs)
        with scaled_policy():
            before=old_result(data)
            with patch.object(mixed_intake.Reader,'visit',fail_extended):
                actual=mixed_intake.inspect_container(data,'module.py')
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])
        self.assertEqual(actual['segments'][0]['structural_partition']['capacity_fallback']['reason'],'RESOURCE_LIMIT')

    def test_new_syntax_failure_is_not_restored_by_later_budget_fallback(self):
        valid=padded(functions(4,'valid'))
        invalid=padded(functions(2,'invalid')+'bad = ;\n')
        data=notebook(valid,invalid)
        original=mixed_intake.Reader.visit
        def abort_after_visit(reader,*args,**kwargs):
            result=original(reader,*args,**kwargs)
            if reader.unit_limit==4:raise mixed_intake._ExtensionAborted('CPU_BUDGET')
            return result
        with scaled_policy():
            before=old_result(data,'input.ipynb')
            self.assertEqual(len(before['segments']),2)
            with patch.object(mixed_intake.Reader,'visit',abort_after_visit):
                actual=mixed_intake.inspect_container(data,'input.ipynb')
        self.assertEqual(len(actual['segments']),1)
        self.assertEqual(actual['segments'][0]['cell_index'],0)
        self.assertEqual(actual['segments'][0]['definitions'],before['segments'][0]['definitions'])
        self.assertIn('PYTHON_SYNTAX_ERROR',{g['status'] for g in actual['gaps']})
        self.assertEqual(len([r for r in actual['inventory'] if r['status']=='PYTHON_SEGMENT']),1)

    def test_previous_partition_policy_remains_readable(self):
        data=padded(functions(1))
        parsed=mixed_intake.inspect_container(data,'module.py')
        parsed['version']='mixed-intake-6-fence-analysis-view'
        parsed['segments'][0]['structural_partition']['policy']='complete-top-level-units-1'
        source={'provider':'local','file_id':'module.py','path':'module.py','size':len(data),
                'sha256':sha(data),'source_url':'local://module.py'}
        profile={'schema':1,'id':'old-partition','title':'Old partition',
                 'targets':[{'api':'hashlib.sha256','capability':'digest'}]}
        with patch.object(corpus,'_parser',return_value=copy.deepcopy(parsed)):
            result=corpus.inspect_one(data,source,Path('/unused'),profile,None)
        self.assertIs(validate_result(result,source),result)
        self.assertEqual(result['segments'][0]['structural_partition']['policy'],'complete-top-level-units-1')


if __name__=='__main__':
    unittest.main()
