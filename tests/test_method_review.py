"""Second review: state, scope, deferred class documentation, bounded super leads."""
import unittest
from forge_core import corpus
from test_research_retrieval import report_from

CODE='''class Base:
    """Parent behavior is context, not proof."""
    def __call__(self, value): return value
class Child(Base):
    """Delegating child."""
    def __call__(self, value): return super().__call__(value)
'''
def row(code,name):
    r=report_from([('m.py',code)])
    return r,next(x for x in corpus.rows(r) if x['name']==name)
def superrefs(code=CODE):
    r,d=row(code,'Child.__call__');return corpus.dependency_context(r,d['definition_id'])['references']
class MethodReviewTests(unittest.TestCase):
    def test_augmented_attribute(self):
        r,d=row('def f(state):\n    state.pending_count += 1\n','f')
        a=[x for x in d['attribute_reads'] if x['name']=='state.pending_count'];self.assertEqual(len(a),1);self.assertIn('AUGMENTED',a[0]['evidence'])
    def test_script_state_search(self):
        r=report_from([('m.py','result = job.upcoming_sleep\n')]);q=corpus.search(r,'upcoming sleep');self.assertEqual(len(q['results']),1);self.assertEqual(q['results'][0]['record_kind'],'source_scope');self.assertNotIn('definition_id',q['results'][0])
    def test_script_comparison(self):
        r=report_from([('m.py','result = job.elapsed > job.deadline\n')]);self.assertIn('Gt',list(corpus.source_rows(r))[0]['comparison_operators'])
    def test_member_doc_locations(self):
        r=report_from([('note.txt','Notes\n\n```python\nclass C:\n    """documented context"""\n    def f(self): return 1\n```\n')]);d=list(corpus.rows(r))[0];self.assertEqual(d['class_context']['member_documentation_start_line'],5);self.assertEqual(d['class_context']['line_basis'],'SEGMENT_LOCAL_WITH_CONTAINER_MEMBER_OFFSETS')
    def test_stub_no_behavior_boost(self):
        r,d=row('class A:\n    """retry policy"""\n    def __call__(self): ...\n','A.__call__');self.assertEqual(d['implementation_kind'],'DECLARATION_OR_STUB');q=corpus.search(r,'retry policy');self.assertEqual(q['results'][0]['rank_breakdown']['role_preference'],0)
    def test_not_implemented_stub(self):
        _,d=row('class A:\n    def __call__(self): raise NotImplementedError()\n','A.__call__');self.assertEqual(d['implementation_kind'],'DECLARATION_OR_STUB')
    def test_return_none_is_not_assumed_stub(self):
        _,d=row('class A:\n    def __call__(self): return None\n','A.__call__');self.assertEqual(d['implementation_kind'],'BODY_PRESENT_NOT_VERIFIED')
    def test_super_local_lead(self):
        refs=superrefs();x=next(x for x in refs if x['call']=='super().__call__');self.assertEqual([d['name'] for d in x['candidates']],['Base.__call__']);self.assertFalse(x['callee_execution_proven']);self.assertIn('UNRESOLVED',x['status'])
    def test_multiple_inheritance_unresolved(self):
        refs=superrefs(CODE.replace('Child(Base)','Child(Base,Other)'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_shadowed_super_unresolved(self):
        refs=superrefs(CODE.replace('def __call__(self, value): return super()', 'def __call__(self, value, super): return super()'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_decorated_base_unresolved(self):
        refs=superrefs('@rewrite\n'+CODE);self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_rebound_base_unresolved(self):
        refs=superrefs(CODE.replace('class Child(Base):','Base = factory()\nclass Child(Base):'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_cross_file_base_not_inferred(self):
        r=report_from([('a.py',CODE.split('class Child')[0]),('b.py','class Child(Base):\n    def __call__(self,value): return super().__call__(value)\n')]);d=next(x for x in corpus.rows(r) if x['name']=='Child.__call__');refs=corpus.dependency_context(r,d['definition_id'])['references'];self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_super_no_self_unresolved(self):
        refs=superrefs(CODE.replace('def __call__(self, value): return super()', 'def __call__(value): return super()'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_super_not_import_api(self):
        _,d=row(CODE,'Child.__call__');self.assertNotIn('super().__call__',d['declared_api_leads'])
    def test_definition_and_comprehension_reads_separate(self):
        r=report_from([('m.py','def f(s):\n    def g(): return s.hidden_value\n    return s.visible_value\n')]);q=corpus.search(r,'hidden value');parent=next(x for x in q['results'] if x['name']=='f');self.assertFalse(parent['all_query_terms_matched']);self.assertNotIn('hidden',parent['matched_query_terms']);self.assertEqual(q['results'][0]['name'],'f.g')
if __name__=='__main__':unittest.main()
