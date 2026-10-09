import io, zipfile, unittest
from forge_core import corpus
from forge_core.common import Blocked
from test_research_retrieval import report_from

def archive(items):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for path,code in items:z.writestr(path,code)
    return b.getvalue()

class ContextTests(unittest.TestCase):
    def ctx(self,files,name):
        r=report_from(files);d=next(x for x in corpus.rows(r) if x['name']==name)
        return corpus.dependency_context(r,d['definition_id'])
    def test_local_helper(self):
        c=self.ctx([('x.py','def helper(x): return x\ndef use(x): return helper(x)\n')],'use');self.assertEqual(c['references'][0]['candidates'][0]['name'],'helper');self.assertFalse(c['dependency_closure_complete'])
    def test_parameter_does_not_resolve(self):
        c=self.ctx([('x.py','def helper(x): return x\ndef use(helper,x): return helper(x)\n')],'use');self.assertEqual(c['references'][0]['candidates'],[])
    def test_assignment_does_not_resolve(self):
        c=self.ctx([('x.py','def helper(x): return x\ndef use(x):\n    helper = x\n    return helper(x)\n')],'use');self.assertEqual(c['references'][0]['candidates'],[])
    def test_relative_import_same_archive(self):
        c=self.ctx([('p.zip',archive([('pkg/a.py','from .b import helper\ndef use(x): return helper(x)\n'),('pkg/b.py','def helper(x): return x\n')]))],'use');self.assertEqual(c['references'][0]['candidates'][0]['path'],'pkg/b.py')
    def test_absolute_import_lead(self):
        c=self.ctx([('p.zip',archive([('pkg/a.py','from pkg.b import helper\ndef use(x): return helper(x)\n'),('pkg/b.py','def helper(x): return x\n')]))],'use');self.assertEqual(len(c['references'][0]['candidates']),1)
    def test_cross_container_not_joined(self):
        c=self.ctx([('a.py','from b import helper\ndef use(x): return helper(x)\n'),('b.py','def helper(x): return x\n')],'use');self.assertEqual(c['references'][0]['candidates'],[])
    def test_nested_archive_namespace_separate(self):
        inner=archive([('pkg/b.py','def helper(x): return x\n')]);c=self.ctx([('p.zip',archive([('pkg/a.py','from .b import helper\ndef use(x): return helper(x)\n'),('inner.zip',inner)]))],'use');self.assertEqual(c['references'][0]['candidates'],[])
    def test_transcript_declared_module_context(self):
        code='1. pkg/a.py\n```python\nfrom .b import helper\ndef use(x): return helper(x)\n```\n2. pkg/b.py\n```python\ndef helper(x): return x\n```\n'
        c=self.ctx([('notes.txt',code)],'use');self.assertEqual(c['references'][0]['candidates'][0]['path'],'pkg/b.py')
    def test_duplicate_transcript_modules_ambiguous(self):
        code='pkg/a.py\n```python\nfrom .b import helper\ndef use(x): return helper(x)\n```\npkg/b.py\n```python\ndef helper(x): return x\n```\npkg/b.py\n```python\ndef helper(x): return x+1\n```\n'
        c=self.ctx([('notes.txt',code)],'use');self.assertEqual(c['references'][0]['status'],'AMBIGUOUS_CANDIDATES');self.assertEqual(len(c['references'][0]['candidates']),2)
    def test_self_peer_only_candidate(self):
        c=self.ctx([('x.py','class A:\n    def helper(self): return 1\n    def use(self): return self.helper()\n')],'A.use');self.assertEqual(c['references'][0]['candidates'][0]['name'],'A.helper');self.assertFalse(c['references'][0]['callee_execution_proven'])
    def test_bare_method_name_not_class_lookup(self):
        c=self.ctx([('x.py','class A:\n    def helper(self): return 1\n    def use(self): return helper()\n')],'A.use');self.assertEqual(c['references'][0]['candidates'],[])
    def test_nested_function_lexical(self):
        c=self.ctx([('x.py','def outer():\n    def h(): return 1\n    def use(): return h()\n    return use\n')],'outer.use');self.assertEqual(c['references'][0]['candidates'][0]['name'],'outer.h')
    def test_legacy_context_reindex(self):
        r=report_from([('x.py','def x():return 1\n')]);d=r['sources'][0]['segments'][0]['definitions'][0];d.pop('structure');c=corpus.dependency_context(r,d['definition_id']);self.assertIn('REINDEX',c['status'])
    def test_context_unknown_id(self):
        with self.assertRaises(Blocked):corpus.dependency_context(report_from([('x.py','def x():return 1\n')]),'definition_'+'a'*64)
    def test_no_fake_context_id(self):
        with self.assertRaises(Blocked):corpus.dependency_context(report_from([('x.py','print(1)\n')]),'source_'+'a'*64)
    def test_ambiguous_alias_preserved(self):
        c=self.ctx([('p.zip',archive([('pkg/a.py','from .b import h\nfrom .c import h\ndef use(): return h()\n'),('pkg/b.py','def h():return 1\n'),('pkg/c.py','def h():return 2\n')]))],'use');self.assertEqual(c['references'][0]['status'],'AMBIGUOUS_OR_REBOUND_IMPORT')
    def test_local_import_after_call(self):
        c=self.ctx([('x.py','def use():\n    h()\n    from pkg.b import h\n')],'use');self.assertEqual(c['references'][0]['status'],'IMPORT_AFTER_CALL_REQUIRES_REVIEW')
    def test_context_not_source_body(self):
        c=self.ctx([('x.py','def h(): return "PRIVATE_IMPLEMENTATION_BODY_19"\ndef use():return h()\n')],'use');self.assertNotIn('PRIVATE_IMPLEMENTATION_BODY_19',str(c))
    def test_call_lines_original_transcript(self):
        c=self.ctx([('notes.txt','pkg/x.py\n```python\ndef h():return 1\ndef use():return h()\n```\n')],'use');self.assertEqual(c['references'][0]['line'],4)

if __name__=='__main__':unittest.main()
