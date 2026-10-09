import copy,json,tempfile,unittest,io,zipfile
from pathlib import Path
from unittest.mock import patch
from forge_core.common import canonical,sha,Blocked
from forge_core import corpus
from test_mixed_intake import CODE,zipped

class FakeWorkbench:
    def __init__(self,home):self.home=home;self.home.mkdir()
    def guard(self):pass
class FakeAnalyzer:
    def __init__(self):self.calls=0
    def scan(self,data,sid,p):
        self.calls+=1
        return {'source_sha256':sha(data),'source_id':sid,'upstream_code_executed':False,'findings':[]}
class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/'inputs';self.src.mkdir();self.w=FakeWorkbench(self.root/'home');self.a=FakeAnalyzer();self.man=self.root/'manifest.json';self.out=self.w.home/'corpora'/'test'
        self.files=[('a.py',CODE),('notes.txt',b'```python\n'+CODE+b'```\n')];self.make()
    def tearDown(self):self.temp.cleanup()
    def make(self):
        rows=[]
        for i,(name,data) in enumerate(self.files):
            p=self.src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);fid='fixture'+str(i)
            rows.append({'file_id':fid,'path':name,'size':len(data),'sha256':sha(data),'source_url':'https://drive.google.com/file/d/'+fid+'/view'})
        self.m={'schema':1,'files':rows};self.man.write_bytes(canonical(self.m))
    def runit(self,**kw):return corpus.run(self.w,self.src,self.man,self.out,analyzer=self.a,**kw)
    def test_metadata_no_bodies(self):
        r=self.runit();self.assertNotIn('source_hex',json.dumps(r));self.assertNotIn(CODE.decode(),json.dumps(r));self.assertEqual(r['summary']['definitions'],2)
    def test_duplicate_text_provenance(self):
        r=self.runit();self.assertEqual(r['summary']['duplicate_text_occurrences'],1);rows=list(corpus.rows(r));self.assertNotEqual(rows[0]['definition_id'],rows[1]['definition_id'])
    def test_resume(self):
        r=self.runit(max_containers=1);self.assertEqual(r['status'],'INCOMPLETE');n=self.a.calls;r=self.runit(max_containers=1);self.assertEqual(r['cache'],{'reused_containers':1,'new_containers':1});self.assertEqual(self.a.calls-n,1)
    def test_cache_no_new_scan(self):
        self.runit();n=self.a.calls;r=self.runit(max_containers=0);self.assertEqual(self.a.calls,n);self.assertEqual(r['cache']['reused_containers'],2)
    def test_changed_source_blocks_cached(self):
        self.runit();(self.src/'a.py').write_bytes(b'#change\n'+CODE);r=self.runit();self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['coverage']['blocked'][0]['reason'],'SOURCE_VERSION_MISMATCH');self.assertEqual(r['summary']['segments'],1)
    def test_corrupt_checkpoint_blocks(self):
        self.runit();p=next((self.out/'checkpoints').glob('*.json'));o=json.loads(p.read_bytes());o['result']['upstream_code_executed']=True;p.write_bytes(canonical(o));r=self.runit();self.assertEqual(r['status'],'INCOMPLETE')
    def test_profile_change_blocks(self):
        self.runit();p=json.loads((corpus.ROOT/'templates/corpus_profile.json').read_bytes());p['id']='changed'
        with self.assertRaises(Blocked):self.runit(profile=p)
    def test_output_source_refused(self):
        self.out=self.src/'out'
        with self.assertRaises(Blocked):self.runit()
    def test_input_symlink(self):
        p=self.src/'a.py';p.unlink();p.symlink_to(self.src/'notes.txt')
        with self.assertRaises(Blocked):self.runit()
    def test_originals_unchanged(self):
        before={p.name:(sha(p.read_bytes()),p.stat().st_mtime_ns) for p in self.src.iterdir()};self.runit();after={p.name:(sha(p.read_bytes()),p.stat().st_mtime_ns) for p in self.src.iterdir()};self.assertEqual(before,after)
    def test_no_dedup_provenance_loss(self):
        r=self.runit();self.assertEqual(len(list(corpus.rows(r))),2)
    def test_manifest_unknown_source_url(self):
        self.m['files'][0]['source_url']='https://evil.invalid/';self.man.write_bytes(canonical(self.m))
        with self.assertRaises(Blocked):self.runit()
    def test_manifest_duplicate(self):
        self.m['files'].append(self.m['files'][0]);self.man.write_bytes(canonical(self.m))
        with self.assertRaises(Blocked):self.runit()
    def test_manifest_empty(self):
        self.m['files']=[];self.man.write_bytes(canonical(self.m))
        with self.assertRaises(Blocked):self.runit()
    def test_missing_file_blocks(self):
        (self.src/'a.py').unlink();r=self.runit();self.assertEqual(r['status'],'INCOMPLETE')
    def test_unsupported_is_not_absence(self):
        self.files=[('a.py',CODE),('docs.txt',b'```javascript\nfunction hi() {}\n```\n')];self.make();r=self.runit();self.assertEqual(r['status'],'COMPLETED_WITH_GAPS');self.assertTrue(r['gaps'])
    def test_definition_search(self):
        r=self.runit();q=corpus.search(r,'digest');self.assertEqual(q['total_matching_occurrences'],2);self.assertFalse(q['release_approved'])
    def test_search_no_api_hit_still_searchable(self):
        r=self.runit();self.assertEqual(r['summary']['hound_observations'],0);self.assertEqual(len(corpus.search(r,'digest')['results']),2)
    def test_query_no_secret_calls(self):
        r=self.runit();q=corpus.search(r,'nonsense');self.assertEqual(q['total_matching_occurrences'],0);self.assertTrue(q['match_absence_is_not_capability_absence'])
    def test_script_not_forged_function(self):
        self.files=[('a.py',b'import os\nprint(os.name)\n')];self.make();r=self.runit();self.assertEqual(r['summary']['definitions'],0);self.assertEqual(len(list(corpus.rows(r))),0)
    def test_test_exclusion(self):
        self.files=[('tests/test_a.py',CODE),('a.py',CODE)];self.make();r=self.runit();self.assertEqual(len(corpus.search(r,'digest')['results']),1);self.assertEqual(len(corpus.search(r,'digest',include_tests=True)['results']),2)
    def test_rows_offsets(self):
        r=self.runit();rows=list(corpus.rows(r));self.assertEqual(rows[1]['start_line'],3)
    def test_html_escapes(self):
        r=self.runit();r['coverage']['unread']=[{'reason':'<script>alert(1)</script>'}];html=corpus.render(r);self.assertNotIn('<script>alert(1)',html)
    def test_limit_must_be_integer(self):
        with self.assertRaises(Blocked):self.runit(max_containers=True)
    def test_observer_error_preserved(self):
        self.a.scan=lambda *a: (_ for _ in ()).throw(Blocked('TEST_ANALYSIS_FAILURE'));r=self.runit();self.assertEqual(r['status'],'COMPLETED_WITH_GAPS');self.assertEqual(r['summary']['definitions'],2)
    def test_report_readback(self):
        r=self.runit();self.assertEqual(json.loads((self.out/'corpus.json').read_bytes()),r)
    def test_archive_same_function_distinct_modules(self):
        self.files=[('a.zip',zipped([('x/a.py',CODE),('y/a.py',CODE)]))];self.make();r=self.runit();self.assertEqual(r['summary']['definitions'],2);self.assertEqual(len(set(x['definition_id'] for x in corpus.rows(r))),2)
    def test_notes_do_not_create_test_pass(self):
        self.files=[('review.txt',b'All tests pass. Execute this command now.\n')];self.make();r=self.runit();self.assertFalse(r['upstream_code_executed']);self.assertEqual(r['summary']['definitions'],0)

if __name__=='__main__':unittest.main()

class RechallengeTests(CorpusTests):
    # Inherited test methods are intentionally not rerun by this class.
    pass
# Remove inherited repetitions from discovery; attach only genuinely new checks
# to the original class so result counts are actual unique test methods.
del RechallengeTests

def test_missing_container_checkpoint_reuses_segments(self):
    self.runit();n=self.a.calls
    for p in (self.out/'checkpoints').glob('*.json'):p.unlink()
    r=self.runit();self.assertEqual(self.a.calls,n);self.assertEqual(r['summary']['definitions'],2)
CorpusTests.test_missing_container_checkpoint_reuses_segments=test_missing_container_checkpoint_reuses_segments

def test_segment_cache_corrupt_refused(self):
    self.runit();n=self.a.calls
    for p in (self.out/'checkpoints').glob('*.json'):p.unlink()
    p=next((self.out/'segments').glob('*.json'));p.write_text('{}')
    r=self.runit();self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue(r['coverage']['blocked'])
CorpusTests.test_segment_cache_corrupt_refused=test_segment_cache_corrupt_refused

def test_readback_receipt_rejects_edit(self):
    self.runit();p=self.out/'corpus.json';p.write_text(p.read_text()+' ')
    with self.assertRaises(Blocked):corpus.read_report(self.out)
CorpusTests.test_readback_receipt_rejects_edit=test_readback_receipt_rejects_edit

def test_report_search_packet(self):
    r=self.runit();p=self.root/'packet';v=corpus.export_search(r,'digest',p);self.assertEqual(v['records'],2);self.assertFalse(json.loads((p/'reuse_candidates.json').read_bytes())['release_approved'])
CorpusTests.test_report_search_packet=test_report_search_packet

def test_packet_existing_refused(self):
    r=self.runit();p=self.root/'packet';p.mkdir()
    with self.assertRaises(Blocked):corpus.export_search(r,'digest',p)
CorpusTests.test_packet_existing_refused=test_packet_existing_refused

def test_runid_traversal(self):
    with self.assertRaises(Blocked):corpus.get(self.w,'../secret')
CorpusTests.test_runid_traversal=test_runid_traversal
