"""Frozen first acceptance set for task-specific, source-bound reuse decisions."""
import copy,json,tempfile,unittest
from pathlib import Path
from forge_core.common import Blocked,canonical,sha
from forge_core.store import Store
from forge_core import policy


def seed(store):
    m=policy.template('reliability');mid=store.create_mission(m)
    source='https://github.com/example/project/blob/'+'1'*40+'/helper.py'
    profile=sha(canonical(m['profile']))
    f={'finding_id':'probe_'+ 'a'*64,'source_id':source,'source_sha256':'2'*64,
       'registry_sha256':profile,'qualified_name':'fingerprint','function_lines':[1,5],
       'scope_kind':'function','resolved_api':'hashlib.sha256','capability':'digest',
       'status':'DECLARED_API_CALL_CANDIDATE','runtime_verified':False,'release_approved':False,
       'limitations':['Static observation only.'],'evidence':[{'line_start':3,'line_end':3,'observation':'declared call'}]}
    candidate={'source_id':source,'repository':'example/project','repository_id':1,'commit':'1'*40,
       'path':'helper.py','source_sha256':'2'*64,'git_blob_sha1':'3'*40,'declared_license':'MIT',
       'rights_status':'DECLARATIONS_ONLY_REVIEW_REQUIRED','findings':[f],'functions_inspected':1,
       'provenance':'synthetic_fixture','profile_sha256':profile,'runtime_verified':False,
       'reuse_approved':False,'release_approved':False}
    with store.tx() as c:
        c.execute('INSERT INTO candidate VALUES(?,?,?,?,?)',('c_fixture',mid,source,canonical(candidate),sha(canonical(candidate))))
        c.execute('UPDATE task SET state="DONE",result=? WHERE mission=?',(canonical({'outcome':'synthetic_fixture'}),mid))
    return mid,candidate


def contract():
    return {'schema':1,'title':'Fingerprint JSON evidence','objective':'Stable fingerprints; reject invalid evidence.',
      'requirements':[
        {'id':'digest','label':'Declared SHA-256 construction','kind':'observed_api','required':True,'apis':['hashlib.sha256']},
        {'id':'order','label':'Key-order independent','kind':'behavior_test','required':True},
        {'id':'context','label':'Dependencies and limitations reviewed','kind':'review','required':True}]}


class ReuseContractTests(unittest.TestCase):
    def setUp(self):
        from forge_core.reuse import Casebook
        self.tmp=tempfile.TemporaryDirectory();self.s=Store.create(Path(self.tmp.name)/'home',{})
        self.mid,self.source=seed(self.s);self.book=Casebook(self.s);self.case=self.book.create(self.mid,contract())
        self.key=self.case['candidates'][0]['key']
    def tearDown(self):self.tmp.cleanup()
    def note(self,**kw):
        v={'candidate':self.key,'requirement':'context','verdict':'SUPPORTED','note':'Known imports inspected; runtime environment not qualified.',
           'actor':'operator','supersedes':None}
        v.update(kw);return v
    def test_syntax_is_not_behavior(self):
        c=self.book.compare(self.case['id']);r=c['rows'][0]
        self.assertEqual([x['state'] for x in r['requirements']],['OBSERVED','UNKNOWN','UNKNOWN'])
        self.assertEqual(r['verdict'],'NEEDS_EVIDENCE');self.assertFalse(c['release_approved'])
    def test_missing_api_unknown(self):
        v=contract();v['requirements'][0]['apis']=['hashlib.file_digest']
        c=self.book.create(self.mid,v);self.assertEqual(self.book.compare(c['id'])['rows'][0]['requirements'][0]['state'],'UNKNOWN')
    def test_review_does_not_run_tests(self):
        self.book.record(self.case['id'],self.note());r=self.book.compare(self.case['id'])['rows'][0]
        self.assertEqual(r['verdict'],'NEEDS_EVIDENCE');self.assertFalse(r['runtime_verified'])
    def test_cannot_manually_pass_behavior(self):
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(requirement='order'))
    def test_replay_idempotent(self):
        a=self.book.record(self.case['id'],self.note());b=self.book.record(self.case['id'],self.note())
        self.assertEqual(a,b);self.assertEqual(len(self.book.history(self.case['id'])),1)
    def test_revision_requires_current_head(self):
        a=self.book.record(self.case['id'],self.note())
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(verdict='CONTRADICTED',note='Missing helper.'))
        b=self.book.record(self.case['id'],self.note(verdict='CONTRADICTED',note='Missing helper.',supersedes=a['id']))
        self.assertEqual(len(self.book.history(self.case['id'])),2)
        self.assertEqual(self.book.compare(self.case['id'])['rows'][0]['verdict'],'CONTRADICTED')
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(note='Late revision.',supersedes=a['id']))
    def test_unknown_retained(self):
        self.book.record(self.case['id'],self.note(verdict='UNKNOWN'))
        self.assertEqual(self.book.compare(self.case['id'])['rows'][0]['verdict'],'NEEDS_EVIDENCE')
    def test_stale_source(self):
        v=copy.deepcopy(self.source);v['source_sha256']='4'*64;v['findings'][0]['source_sha256']='4'*64
        with self.s.tx() as c:c.execute('UPDATE candidate SET result=?,result_hash=? WHERE id=?',(canonical(v),sha(canonical(v)),'c_fixture'))
        r=self.book.compare(self.case['id']);self.assertEqual(r['status'],'STALE_SOURCE_REVIEW_REQUIRED')
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note())
    def test_contract_is_immutable(self):
        a=self.book.create(self.mid,contract());self.assertEqual(a['id'],self.case['id'])
        v=contract();v['objective']='A different requirement.';b=self.book.create(self.mid,v)
        self.assertNotEqual(a['id'],b['id'])
    def test_restart(self):
        self.book.record(self.case['id'],self.note())
        from forge_core.reuse import Casebook
        b=Casebook(Store(self.s.home));self.assertEqual(self.book.compare(self.case['id']),b.compare(self.case['id']))
    def test_source_binding_tamper(self):
        v=copy.deepcopy(self.source);v['findings'][0]['source_sha256']='a'*64
        with self.s.tx() as c:c.execute('UPDATE candidate SET result=?,result_hash=?',(canonical(v),sha(canonical(v))))
        with self.assertRaises(Blocked):self.book.create(self.mid,contract())
    def test_fabricated_function_rejected(self):
        v=copy.deepcopy(self.source);v['findings'][0]['scope_kind']='source'
        with self.s.tx() as c:c.execute('UPDATE candidate SET result=?,result_hash=?',(canonical(v),sha(canonical(v))))
        with self.assertRaises(Blocked):self.book.create(self.mid,contract())
    def test_unknown_candidate(self):
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(candidate='nobody'))
    def test_extra_authority(self):
        for k in ['release_approved','runtime_verified','command','execute_source']:
            with self.subTest(k=k),self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(**{k:True}))
    def test_empty_contract(self):
        v=contract();v['requirements']=[]
        with self.assertRaises(Blocked):self.book.create(self.mid,v)
    def test_duplicate_requirements(self):
        v=contract();v['requirements'].append(v['requirements'][0])
        with self.assertRaises(Blocked):self.book.create(self.mid,v)
    def test_required_boolean(self):
        v=contract();v['requirements'][0]['required']=1
        with self.assertRaises(Blocked):self.book.create(self.mid,v)
    def test_wildcard_not_rule(self):
        v=contract();v['requirements'][0]['apis']=['*']
        with self.assertRaises(Blocked):self.book.create(self.mid,v)
    def test_export_and_readback(self):
        p=Path(self.tmp.name)/'packet';receipt=self.book.export(self.case['id'],p)
        for n,h in receipt['files'].items():self.assertEqual(sha((p/n).read_bytes()),h)
        self.assertTrue((p/'SOURCE_LOCK.json').exists());self.assertTrue((p/'Integration_Plan.md').exists())
        with self.assertRaises(Blocked):self.book.export(self.case['id'],p)
    def test_empty_note(self):
        with self.assertRaises(Blocked):self.book.record(self.case['id'],self.note(note=''))
    def test_one_required_criterion(self):
        v=contract()
        for r in v['requirements']:r['required']=False
        with self.assertRaises(Blocked):self.book.create(self.mid,v)
    def test_render_escapes(self):
        v=contract();v['title']='<script>alert(1)</script>';c=self.book.create(self.mid,v)
        out=Path(self.tmp.name)/'html';self.book.export(c['id'],out)
        txt=(out/'Review.html').read_text();self.assertNotIn('<script>alert',txt);self.assertIn('&lt;script&gt;',txt)
