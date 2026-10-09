"""Frozen recovery contract: deterministic faults; target source is never executed."""
import copy,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core.common import Blocked,canonical,sha
from forge_core import corpus
from forge_core.mixed_intake import inspect_container

A=b'import hashlib\ndef digest(value):\n    return hashlib.sha256(value).hexdigest()\n'
B=b'def other(value):\n    return value + 1\n'
class Workbench:
    def __init__(self,home):self.home=home;home.mkdir()
    def guard(self):pass
class Probe:
    def __init__(self,failures=1,reason='TOOL_TIMEOUT'):
        self.failures=failures;self.reason=reason;self.calls=[];self.lock=threading.Lock()
    def scan(self,body,sid,profile):
        with self.lock:
            self.calls.append((sid,sha(body)))
            if body==A and self.failures:
                self.failures-=1
                if self.reason=='INTERRUPT':raise KeyboardInterrupt('intentional test interruption')
                raise Blocked(self.reason)
        return {'source_id':sid,'source_sha256':sha(body),'upstream_code_executed':False,'findings':[]}
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'src';self.src.mkdir()
        self.w=Workbench(self.root/'home');self.out=self.w.home/'corpora'/'trial';self.man=self.root/'manifest.json'
        self.files=[('a.py',A),('b.py',B)];self.prepare()
        self.p=Probe();self.parser=patch.object(corpus,'_parser',lambda body,name,home:inspect_container(body,name));self.parser.start()
    def tearDown(self):self.parser.stop();self.tmp.cleanup()
    def prepare(self):
        import zipfile,io
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in self.files:z.writestr(n,b)
        data=buf.getvalue();(self.src/'code.zip').write_bytes(data)
        self.rows=[{'provider':'synthetic','file_id':'self-source','source_url':'fixture://self-source','path':'code.zip','size':len(data),'sha256':sha(data)}]
        self.man.write_bytes(canonical({'schema':1,'files':self.rows}))
    def runit(self,**kwargs):return corpus.run(self.w,self.src,self.man,self.out,analyzer=self.p,**kwargs)
    def test_transient_failure_recovers_on_next_invocation(self):
        first=self.runit();self.assertEqual(first['status'],'COMPLETED_WITH_GAPS')
        second=self.runit();self.assertEqual(second['summary']['hound_segments'],2)
        self.assertEqual(second['status'],'COMPLETED_FOR_SELECTED_CONTAINERS')
    def test_successful_sibling_not_rescanned(self):
        first=self.runit();before=list(self.p.calls);second=self.runit()
        self.assertEqual(len(self.p.calls)-len(before),1)
        self.assertEqual(sum(h==sha(B) for _,h in self.p.calls),1)
        self.assertEqual(second['summary']['definitions'],first['summary']['definitions'])
    def test_retry_has_finite_persistent_total(self):
        self.p=Probe(100)
        for _ in range(6):r=self.runit()
        self.assertEqual(sum(h==sha(A) for _,h in self.p.calls),3)
        self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
        self.assertEqual(r['recovery']['exhausted_segments'],1)
    def test_zero_container_budget_does_not_retry(self):
        self.runit();n=len(self.p.calls);r=self.runit(max_containers=0)
        self.assertEqual(len(self.p.calls),n);self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
    def test_permanent_failure_not_blindly_retried(self):
        self.p=Probe(100,'SOURCE_LIMIT');self.runit();n=len(self.p.calls)
        for _ in range(4):r=self.runit()
        self.assertEqual(len(self.p.calls),n);self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
    def test_binding_failure_not_retried(self):
        self.p=Probe(100,'HOUND_BINDING');self.runit();n=len(self.p.calls);self.runit();self.assertEqual(len(self.p.calls),n)
    def test_source_change_blocks_recovery(self):
        self.runit();n=len(self.p.calls);(self.src/'code.zip').write_bytes(b'changed')
        r=self.runit();self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(self.p.calls),n)
        self.assertEqual(r['coverage']['blocked'][0]['reason'],'SOURCE_VERSION_MISMATCH')
    def test_profile_change_requires_new_job(self):
        self.runit();p=json.loads((corpus.ROOT/'templates/corpus_profile.json').read_text());p['id']='new'
        with self.assertRaises(Blocked):self.runit(profile=p)
    def test_corrupted_container_never_used_to_retry(self):
        self.runit();n=len(self.p.calls);cp=next((self.out/'checkpoints').glob('*.json'));cp.write_bytes(b'{}')
        r=self.runit();self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(self.p.calls),n)
    def test_success_cache_bytes_preserved(self):
        self.runit();old={p.name:p.read_bytes() for p in (self.out/'segments').glob('*.json')};self.runit()
        for n,b in old.items():self.assertEqual((self.out/'segments'/n).read_bytes(),b)
        self.assertEqual(len(list((self.out/'segments').glob('*.json'))),2)
    def test_cached_success_no_new_work(self):
        self.p=Probe(0);self.runit();n=len(self.p.calls);r=self.runit()
        self.assertEqual(n,len(self.p.calls));self.assertEqual(r['status'],'COMPLETED_FOR_SELECTED_CONTAINERS')
    def test_failure_history_survives_success(self):
        self.runit();self.runit();r=self.runit()
        self.assertEqual(r['recovery']['recovered_segments'],1)
        entries=r['recovery']['segments'];fail=[x for x in entries if x['attempt_count']==2]
        self.assertEqual(len(fail),1);self.assertEqual([x['state'] for x in fail[0]['attempts']],['FAILED','SUCCEEDED'])
        self.assertEqual(fail[0]['attempts'][0]['reason'],'TOOL_TIMEOUT')
    def test_interrupted_start_consumes_attempt(self):
        self.p=Probe(1,'INTERRUPT')
        with self.assertRaises(KeyboardInterrupt):self.runit()
        r=self.runit();self.assertEqual(r['status'],'COMPLETED_FOR_SELECTED_CONTAINERS')
        attempts=[x for x in r['recovery']['segments'] if x['attempt_count']==2]
        self.assertEqual(len(attempts),1);self.assertEqual(attempts[0]['attempts'][0]['reason'],'ATTEMPT_INTERRUPTED')
    def test_repeated_interruption_exhausts_budget(self):
        self.p=Probe(100,'INTERRUPT')
        for _ in range(6):
            try:r=self.runit()
            except KeyboardInterrupt:continue
        self.assertEqual(sum(h==sha(A) for _,h in self.p.calls),3)
        self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
    def test_parser_only_gap_does_not_reinspect(self):
        self.files=[('a.py',A),('script.js',b'function x(){}')];self.prepare();self.p=Probe(0)
        self.runit();n=len(self.p.calls);r=self.runit();self.assertEqual(n,len(self.p.calls));self.assertEqual(r['status'],'COMPLETED_WITH_GAPS')
    def test_no_source_payload_in_recovery(self):
        self.runit();r=self.runit();text=json.dumps(r)
        self.assertNotIn(A.decode(),text);self.assertNotIn(A.hex(),text)
        self.assertFalse(r['upstream_code_executed']);self.assertFalse(r['release_approved'])
    def test_old_failed_checkpoint_preserved_as_revision(self):
        self.runit();old=next((self.out/'checkpoints').glob('*.json')).read_bytes();self.runit()
        history=list((self.out/'recovery_history').glob('*.json'))
        self.assertTrue(history);self.assertIn(old,[p.read_bytes() for p in history])
    def test_io_failure_is_retryable(self):
        self.p=Probe(1,'TOOL_IO_FAILED');self.runit();r=self.runit();self.assertEqual(r['summary']['hound_segments'],2)
    def test_cleanup_failure_is_not_retryable(self):
        self.p=Probe(100,'TOOL_CLEANUP_FAILED');self.runit();n=len(self.p.calls);r=self.runit();self.assertEqual(len(self.p.calls),n)
    def test_derived_search_does_not_invent_execution(self):
        self.runit();r=self.runit();rows=list(corpus.rows(r));self.assertEqual(len(rows),2)
        for x in rows:self.assertEqual(x['runtime_status'],'NOT_RUN')
if __name__=='__main__':unittest.main()
