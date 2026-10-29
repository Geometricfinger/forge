"""Newcomer paths: plain folders, plain-text notes, default contract, packet refusals, readable output, error docs."""
import json,re,subprocess,sys,tempfile,unittest
from pathlib import Path
from forge_core.workspace import ROOT
from forge_core import corpus
from forge_core.common import Blocked

def forge(home,*args):
    p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(home),*args],capture_output=True,text=True,timeout=120)
    return p.returncode,p.stdout

class Usability(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.d=Path(self.t.name);self.home=self.d/'home'
        if self._testMethodName!='test_human_and_pretty_output':forge(self.home,'init')
    def tearDown(self):self.t.cleanup()
    def j(self,*a):
        rc,out=forge(self.home,*a);return rc,json.loads(out)

    def test_plain_folder_corpus_run_without_manifest(self):
        rc,r=self.j('corpus-run','--source-root',str(ROOT/'examples/hello_local'),'--run-id','plain')
        self.assertEqual(rc,0,r);self.assertEqual(r['status'],'COMPLETED_FOR_SELECTED_CONTAINERS');self.assertFalse(r['upstream_code_executed'])
        self.assertEqual(r['summary']['definitions'],2)
        rc,s=self.j('corpus-search','--run-id','plain','--query','subprocess.run','--distinct')
        self.assertEqual(rc,0);self.assertGreaterEqual(s['full_match_count'],1)
        # re-running the same id on the same folder is accepted (cached)
        rc,r2=self.j('corpus-run','--source-root',str(ROOT/'examples/hello_local'),'--run-id','plain');self.assertEqual(rc,0,r2)

    def test_plain_folder_changed_contents_need_new_run_id(self):
        src=self.d/'src';src.mkdir();(src/'a.py').write_text('def f():\n    return 1\n')
        self.assertEqual(self.j('corpus-run','--source-root',str(src),'--run-id','x')[0],0)
        (src/'b.py').write_text('def g():\n    return 2\n')
        rc,r=self.j('corpus-run','--source-root',str(src),'--run-id','x');self.assertEqual(rc,2);self.assertEqual(r['reason'],'CORPUS_BINDING_CHANGED_NEW_RUN_REQUIRED')

    def test_local_rows_skip_hidden_and_reject_empty(self):
        src=self.d/'src';(src/'.git').mkdir(parents=True);(src/'.git'/'x.py').write_text('x=1\n');(src/'bin.exe').write_bytes(b'\0')
        with self.assertRaises(Blocked):corpus.local_rows(src)
        (src/'ok.py').write_text('y=1\n');rows=corpus.local_rows(src)
        self.assertEqual([r['path'] for r in rows],['ok.py']);self.assertEqual(corpus.manifest({'schema':1,'files':rows})['files'],rows)

    def test_local_provider_url_is_checked(self):
        row=dict(corpus.local_rows(ROOT/'examples/hello_local')[0]);row['source_url']='https://example.com/x'
        with self.assertRaises(Blocked):corpus.manifest({'schema':1,'files':[row]})

    def test_readme_manifest_example_is_current_and_runs(self):
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        block=re.search(r'<!-- manifest-example -->\n```json\n(.*?)\n```',readme,re.S).group(1)
        shipped=json.loads((ROOT/'examples/hello_local.manifest.json').read_text())
        self.assertEqual(json.loads(block),shipped)
        self.assertEqual(shipped['files'],corpus.local_rows(ROOT/'examples/hello_local'))
        rc,r=self.j('corpus-run','--source-root',str(ROOT/'examples/hello_local'),'--manifest',str(ROOT/'examples/hello_local.manifest.json'),'--run-id','hello','--profile',str(ROOT/'templates/corpus_profile.json'))
        self.assertEqual(rc,0,r)

    def test_make_manifest(self):
        out=self.d/'m.json';rc,r=self.j('make-manifest',str(ROOT/'examples/hello_local'),'--out',str(out))
        self.assertEqual((rc,r['status'],r['files']),(0,'MANIFEST_WRITTEN',2))
        self.assertEqual(json.loads(out.read_text()),json.loads((ROOT/'examples/hello_local.manifest.json').read_text()))
        self.assertEqual(self.j('make-manifest',str(ROOT/'examples/hello_local'),'--out',str(out))[1]['reason'],'OUTPUT_CONFLICT')

    def test_opportunity_draft_plain_text_and_json(self):
        rc,r=self.j('opportunity-draft','--file',str(ROOT/'examples/hello_local/notes.md'))
        self.assertEqual(rc,0,r);self.assertEqual(r['status'],'DRAFT_CUES_NOT_CLAIMS')
        self.assertIn('manual',{x['cue'] for x in r['suggestions']});self.assertIn('limitation',{x['cue'] for x in r['suggestions']})
        rec={'id':'n1','text':'We must re-enter totals manually.','kind':'firsthand','public':False,'origin_group':'g','uri':'file-ref://local/n1','observed_on':'2026-01-01','derived_from':[]}
        f=self.d/'n.json';f.write_text(json.dumps(rec));rc,r=self.j('opportunity-draft','--file',str(f))
        self.assertEqual((rc,r['source']),(0,'n1'))
        f.write_text('{not json');self.assertEqual(self.j('opportunity-draft','--file',str(f))[1]['reason'],'INVALID_JSON')

    def test_default_reuse_contract_is_valid(self):
        from forge_core.reuse import validate_contract
        from forge_core.common import loads,read
        validate_contract(loads(read(ROOT/'templates/reuse_contract.json')))

    def test_review_new_default_contract_reaches_mission_check(self):
        rc,m=self.j('new','--query','json fingerprint')
        rc,r=self.j('review-new',m['mission'])
        self.assertEqual(r['reason'],'NO_SOURCE_CANDIDATES')  # contract parsed; only the missing run blocks

    def test_packet_fresh_then_explained_refusal(self):
        rc,m=self.j('new','--query','json fingerprint');mid=m['mission']
        rc,p=self.j('packet',mid,'--out',str(self.d/'p1.json'));self.assertEqual(rc,0,p);self.assertEqual(p['kind'],'search')
        rc,r=self.j('packet',mid,'--out',str(self.d/'p2.json'))
        self.assertEqual((rc,r['reason']),(2,'NO_AVAILABLE_REQUEST_CHECK_STATUS'))
        self.assertEqual(r['detail']['task_counts']['LEASED'],1);self.assertIn('accept',r['detail']['explanation'])
        self.assertFalse((self.d/'p2.json').exists())

    def test_human_and_pretty_output(self):
        rc,out=forge(self.home,'--human','init');self.assertEqual(rc,0);self.assertTrue(out.startswith('Status: INITIALIZED'))
        rc,out=forge(self.home,'contract-show','--pretty');self.assertEqual(rc,0);self.assertIn('\n  ',out);json.loads(out)
        rc,out=forge(self.home,'packet','m_nope','--out',str(self.d/'x.json'),'--human');self.assertEqual(rc,2)
        self.assertIn('Status: BLOCKED',out);self.assertIn('ERROR_CODES.md',out)

    def test_every_error_code_documented(self):
        p=subprocess.run([sys.executable,str(ROOT/'tools/gen_error_codes.py'),'--check'],capture_output=True,text=True,timeout=60)
        self.assertEqual(p.returncode,0,p.stdout);self.assertIn('docs/ERROR_CODES.md',(ROOT/'README.md').read_text(encoding='utf-8'))
