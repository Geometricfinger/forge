"""Additional post-result review: malformed metadata and deceptive scope."""
import copy, unittest
from forge_core import corpus
from forge_core.common import Blocked
from test_github_corpus import capsule,validate,inspect
from test_method_review import CODE,superrefs,row
from test_research_retrieval import report_from

class Round3ReviewTests(unittest.TestCase):
    def test_nonstring_selected_path(self):
        _,r=capsule();r['selected_paths']=[{}]
        with self.assertRaises(Blocked):validate(r)
    def test_null_selected_path(self):
        _,r=capsule();r['selected_paths']=[None]
        with self.assertRaises(Blocked):validate(r)
    def test_nonlist_selected_paths(self):
        _,r=capsule();r['selected_paths']='pkg/a.py'
        with self.assertRaises(Blocked):validate(r)
    def test_sensitive_member(self):
        _,r=capsule([('credentials.py',b'x=1')])
        with self.assertRaises(Blocked):validate(r)
    def test_vendor_member(self):
        _,r=capsule([('site-packages/a.py',b'x=1')])
        with self.assertRaises(Blocked):validate(r)
    def test_member_size_changed(self):
        b,r=capsule();r['members'][0]['size']+=1
        with self.assertRaises(Blocked):inspect(b,r)
    def test_repo_query_injection(self):
        _,r=capsule();r['repository']='example/fixture?token=secret'
        with self.assertRaises(Blocked):validate(r)
    def test_wrong_filename_format(self):
        _,r=capsule();r['path']='not-python.tar'
        with self.assertRaises(Blocked):validate(r)
    def test_import_shadowed_base(self):
        refs=superrefs(CODE.replace('class Child(Base):','from other import Base\nclass Child(Base):'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_duplicate_base_class(self):
        refs=superrefs(CODE.replace('class Child(Base):','class Base:\n    def __call__(self,v): return v\nclass Child(Base):'));self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_conditional_base(self):
        code='if flag:\n    class Base:\n        def __call__(self,v): return v\nclass Child(Base):\n    def __call__(self,v): return super().__call__(v)\n';refs=superrefs(code);self.assertTrue(any(x['call']=='super().__call__' and not x['candidates'] for x in refs))
    def test_super_context_never_execution(self):
        r,d=row(CODE,'Child.__call__');c=corpus.dependency_context(r,d['definition_id']);self.assertFalse(c['dependency_closure_complete']);self.assertFalse(c['source_code_executed']);self.assertFalse(c['release_approved'])
    def test_read_not_observed_api(self):
        r=report_from([('a.py','def f(x): return x.future_delay\n')]);self.assertFalse(corpus.search(r,'future delay',filters={'required_apis':['x.future_delay']})['results'])
    def test_constructor_exact_query(self):
        r=report_from([('a.py','class C:\n    """new policy"""\n    def __init__(self): pass\n    def __call__(self): return 1\n')]);q=corpus.search(r,'C.__init__');self.assertEqual(q['results'][0]['name'],'C.__init__')
    def test_index_unchanged_by_cache_hit_counter(self):
        r=report_from([('a.py','def f(x): return x.delay\n')]);r['cache']={'new_containers':1,'reused_containers':0};a=corpus.search(r,'delay');r['cache']={'new_containers':0,'reused_containers':1};b=corpus.search(r,'delay');self.assertEqual(a['index_binding'],b['index_binding']);self.assertNotEqual(a['receipt_binding'],b['receipt_binding'])
    def test_changed_read_metadata_invalidates_index(self):
        r=report_from([('a.py','def f(x): return x.delay\n')]);a=corpus.search(r,'delay');r['sources'][0]['segments'][0]['definitions'][0]['structure']['attribute_reads'][0]['name']='x.other';b=corpus.search(r,'delay');self.assertNotEqual(a['index_binding'],b['index_binding'])
if __name__=='__main__':unittest.main()
