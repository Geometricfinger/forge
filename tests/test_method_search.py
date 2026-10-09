"""Frozen diagnostic cases for class context, read-state and role-sensitive retrieval.
Only small first-party syntax fixtures are parsed. No source modules are imported.
"""
import copy, unittest
from forge_core import corpus
from forge_core.common import canonical
from test_research_retrieval import report_from

CODE='''class delay_guard:
    """Stop before a cumulative deadline with contention safeguards."""
    def __init__(self, max_delay):
        self.max_delay = max_delay
    def __call__(self, state):
        return state.elapsed_seconds + state.upcoming_sleep >= self.max_delay
class unrelated:
    """A serialization helper."""
    def __call__(self, state):
        return state.payload
'''

def rows(code=CODE):
    return list(corpus.rows(report_from([('m.py',code)])))
def query(text,code=CODE):
    return corpus.search(report_from([('m.py',code)]),text,limit=100)

class MethodSearchTests(unittest.TestCase):
    def test_read_field_retrieval(self):
        q=query('upcoming sleep');self.assertEqual(q['results'][0]['name'],'delay_guard.__call__');self.assertTrue(q['results'][0]['all_query_terms_matched'])
    def test_class_documentation_search(self):
        q=query('contention safeguards');self.assertEqual(q['results'][0]['name'],'delay_guard.__call__')
    def test_behavior_preferred_to_init(self):
        self.assertEqual(query('delay guard')['results'][0]['name'],'delay_guard.__call__')
    def test_explicit_constructor_preferred(self):
        self.assertEqual(query('delay guard init')['results'][0]['name'],'delay_guard.__init__')
    def test_explicit_constructor_word(self):
        self.assertEqual(query('delay guard constructor')['results'][0]['name'],'delay_guard.__init__')
    def test_class_context_has_locations(self):
        c=rows()[1]['class_context'];self.assertEqual(c['name'],'delay_guard');self.assertEqual(c['documentation_start_line'],2);self.assertEqual(c['evidence'],'CLASS_DOCUMENTATION_NOT_BEHAVIOR_PROOF')
    def test_state_read_explained(self):
        r=query('upcoming sleep')['results'][0];self.assertIn('state_read',r['matched_fields']);self.assertEqual(r['observed_apis'],[])
    def test_state_read_not_call(self):
        r=rows()[1];self.assertIn('state.upcoming_sleep',[x['name'] for x in r['attribute_reads']]);self.assertNotIn('state.upcoming_sleep',r['declared_calls'])
    def test_write_is_not_read(self):
        r=rows('def writer(obj):\n    obj.only_written = 1\n');self.assertEqual(r[0]['attribute_reads'],[])
    def test_delete_is_not_read(self):
        self.assertEqual(rows('def remove(obj):\n    del obj.only_deleted\n')[0]['attribute_reads'],[])
    def test_nested_read_not_parent(self):
        q=query('upcoming sleep','def outer():\n    def inner(state):\n        return state.upcoming_sleep\n    return inner\n');self.assertEqual([r['name'] for r in q['results']],['outer.inner'])
    def test_nested_class_not_parent(self):
        code='class Parent:\n    """parenttag"""\n    class Child:\n        """childtag"""\n        def __call__(self): return 1\n';r=rows(code)[0];self.assertEqual(r['class_context']['name'],'Parent.Child');self.assertNotIn('parenttag',r['class_context']['documentation_terms'])
    def test_inner_function_not_class_method(self):
        code='class A:\n    """exclusiveword"""\n    def method(self):\n        def nested(state): return state.limit\n        return nested\n';self.assertEqual(rows(code)[1]['method_role'],'FUNCTION');self.assertIsNone(rows(code)[1]['class_context'])
    def test_inherited_description_not_override_fact(self):
        code='class Parent:\n    """full jitter"""\n    def __call__(self): return 1\nclass Child(Parent):\n    """fixed delay"""\n    def __call__(self): return 0\n';q=query('full jitter',code);self.assertNotIn('Child.__call__',[r['name'] for r in q['results']])
    def test_class_words_cannot_be_observed_api(self):
        r=report_from([('m.py',CODE)]);self.assertFalse(corpus.search(r,'contention',filters={'required_apis':['os.fsync']})['results'])
    def test_compare_records_operator(self):
        row=rows()[1];self.assertIn('GtE',row['comparison_operators']);self.assertEqual(row['runtime_status'],'NOT_RUN')
    def test_same_text_other_run_same_index(self):
        r=report_from([('m.py',CODE)]);r['elapsed_seconds']=1.0;a=corpus.search(r,'delay');r['elapsed_seconds']=9.0;b=corpus.search(r,'delay');self.assertEqual(a['index_binding'],b['index_binding']);self.assertNotEqual(a['receipt_binding'],b['receipt_binding'])
    def test_changed_class_doc_changes_index(self):
        a=query('delay');b=query('delay',CODE.replace('contention safeguards','otherword'));self.assertNotEqual(a['index_binding'],b['index_binding'])
    def test_metadata_not_mutated(self):
        r=report_from([('m.py',CODE)]);a=canonical(r);corpus.search(r,'contention');self.assertEqual(a,canonical(r))
    def test_no_match_remains_empty(self):self.assertEqual(query('quantumbanana')['results'],[])
    def test_doc_never_execution(self):
        q=query('contention');self.assertFalse(q['release_approved']);self.assertEqual(q['runtime_validation'],'NOT_RUN')
    def test_explicit_method_stays_first(self):self.assertEqual(query('__call__')['results'][0]['name'].split('.')[-1],'__call__')
    def test_comments_not_state_reads(self):
        self.assertEqual(query('upcoming sleep','def f():\n    # state.upcoming_sleep\n    return 1\n')['results'],[])
    def test_attribute_bound_visible(self):
        code='def f(o):\n'+''.join('    x=o.attr'+str(i)+'\n' for i in range(150));r=rows(code)[0];self.assertLessEqual(len(r['attribute_reads']),128);self.assertTrue(r['structure_truncated'])

if __name__=='__main__':unittest.main()
