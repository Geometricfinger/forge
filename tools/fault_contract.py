"""Six explicit diagnostics for repair reintroduction; reviewed fixture code only."""
import argparse,io,json,sys,unittest
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
sys.path[:0]=[str(a.root),str(a.root/'tests')]
from forge_core import corpus
from forge_core.common import Blocked
from test_method_search import query
from test_research_retrieval import report_from
from test_method_review import CODE,superrefs
from test_github_corpus import capsule,inspect
class Contract(unittest.TestCase):
 def test_role(self):
  r=query('contention safeguards')['results'];self.assertTrue(r,'required class result absent');self.assertEqual(r[0]['name'],'delay_guard.__call__')
 def test_class_context_retrieval(self):
  r=query('contention safeguards')['results'];self.assertTrue(r,'class documentation is no longer searchable');self.assertIn('class_documentation',r[0]['matched_fields'])
 def test_state_context_retrieval(self):
  r=query('upcoming sleep')['results'];self.assertTrue(r,'state read is no longer searchable');self.assertEqual(r[0]['name'],'delay_guard.__call__')
 def test_blob_guard(self):
  b,r=capsule();r['members'][0]['git_blob_sha1']='0'*40
  with self.assertRaises(Blocked):inspect(b,r)
 def test_shadowed_super_guard(self):
  refs=superrefs(CODE.replace('class Child(Base):','from other import Base\nclass Child(Base):'));found=[x for x in refs if x['call']=='super().__call__'];self.assertTrue(found);self.assertFalse(found[0]['candidates'])
 def test_semantic_identity(self):
  r=report_from([('m.py','def f(x): return x.future_delay\n')]);r['elapsed_seconds']=1;x=corpus.search(r,'delay');r['elapsed_seconds']=99;y=corpus.search(r,'delay');self.assertEqual(x['index_binding'],y['index_binding']);self.assertNotEqual(x['receipt_binding'],y['receipt_binding'])
s=io.StringIO();r=unittest.TextTestRunner(stream=s,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contract));a.out.mkdir(parents=True,exist_ok=False)
d={'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped),'failed_ids':[t.id() for t,e in r.failures]};(a.out/'result.json').write_text(json.dumps(d));(a.out/'tests.log').write_text(s.getvalue());print(json.dumps(d));sys.exit(0 if r.wasSuccessful() else 2)
