"""New frozen checks for source-of-truth, malformed records, and relevance labels."""
import copy,json,stat,unittest,zipfile,io
from unittest.mock import patch
from forge_core import corpus,mixed_intake
from forge_core.common import canonical,sha,Blocked
import test_corpus as helper

class CorpusRecheck(unittest.TestCase):
    setUp=helper.CorpusTests.setUp
    tearDown=helper.CorpusTests.tearDown
    make=helper.CorpusTests.make
    runit=helper.CorpusTests.runit
    def test_end_guard_prevents_publish_after_engine_change(self):
        with patch.object(self.w,'guard',side_effect=[None,Blocked('WORKBENCH_CODE_CHANGED')]):
            with self.assertRaises(Blocked):self.runit()
        self.assertFalse((self.out/'corpus.json').exists())
    def test_checkpoint_list_is_controlled_blocker(self):
        self.runit();p=next((self.out/'checkpoints').glob('*.json'));o=json.loads(p.read_bytes());o['result']=[];o['result_sha256']=sha(canonical([]));p.write_bytes(canonical(o))
        r=self.runit();self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue(r['coverage']['blocked'])
    def test_report_list_is_controlled_blocker(self):
        self.runit();(self.out/'corpus.json').write_bytes(canonical([]));(self.out/'receipt.json').write_bytes(canonical({'report_sha256':sha(canonical([])),'source_bodies_retained':False}))
        with self.assertRaises(Blocked):corpus.read_report(self.out)
    def test_receipt_list_is_controlled_blocker(self):
        self.runit();(self.out/'receipt.json').write_bytes(b'[]')
        with self.assertRaises(Blocked):corpus.read_report(self.out)
    def test_exact_symbol_preferred_to_doc_mention(self):
        self.files=[('code.py',b'def aaa():\n    """fingerprint geometry"""\n    return 1\ndef fingerprint():\n    return 2\n')];self.make();r=self.runit();q=corpus.search(r,'fingerprint')
        self.assertEqual(q['results'][0]['name'],'fingerprint')
    def test_partial_match_warning(self):
        r=self.runit();q=corpus.search(r,'digest unicorn');self.assertEqual(q['full_match_count'],0);self.assertEqual(q['search_status'],'PARTIAL_TERM_MATCHES_ONLY')
    def test_allterm_count(self):
        r=self.runit();q=corpus.search(r,'digest');self.assertEqual(q['full_match_count'],2);self.assertEqual(q['search_status'],'MATCHES_FOUND')
    def test_no_match_status(self):
        q=corpus.search(self.runit(),'unicorn');self.assertEqual(q['search_status'],'NO_METADATA_MATCH')
    def test_ast_node_limit_is_inventory_gap(self):
        r=mixed_intake.inspect_container(b'def f(x):\n    return x+1\n','x.py',{'max_nodes':1});self.assertEqual(r['gaps'][0]['status'],'AST_LIMIT');self.assertEqual(r['segments'],[])
    def test_archive_special_mode_rejected(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            i=zipfile.ZipInfo('pipe.py');i.create_system=3;i.external_attr=(stat.S_IFIFO|0o600)<<16;z.writestr(i,b'def f(): return 1\n')
        r=mixed_intake.inspect_container(b.getvalue(),'x.zip');self.assertEqual(r['segments'],[]);self.assertEqual(r['gaps'][0]['status'],'UNSAFE_MEMBER')
    def test_unknown_format_explicit_gap(self):
        r=mixed_intake.inspect_container(b'not parsed','x.xyz');self.assertEqual(r['gaps'][0]['status'],'UNSUPPORTED_FORMAT')

if __name__=='__main__':unittest.main()
