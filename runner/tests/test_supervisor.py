"""Preimplementation contract. Mocked acquisition/retrieval; actual I/O and ledger."""
import copy, hashlib, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from research_runner import supervise as fs
from research_runner import runner, bridge, core

class SupervisorContract(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name)
        self.src=self.root/'src';self.src.mkdir();self.forge=self.root/'engine';self.forge.mkdir();self.work=self.root/'mission'
        text=b'def useful(x):\n    return x\n';(self.src/'x.py').write_bytes(text)
        self.source={'id':'s','path':'x.py','kind':'implementation','origin':'fixture://example','capture':'synthetic_fixture','family':'fixture','size':len(text),'sha256':core.sha(text)}
        self.m={'schema':1,'objective':'Test bounded follow-up','sources':[self.source], 'questions':[{'id':'q0','query':'useful','intent':'code','sources':['s']}], 'budgets':{'max_sources':2,'max_source_bytes':8000000,'max_total_bytes':8000000,'max_questions':3,'packet_bytes':4096}}
        self.mp=self.root/'mission.json';self.mp.write_bytes(core.canonical(self.m))
        self.record={'id':'definition1','source_id':'s','source_sha256':core.sha(text),'origin':'fixture://example','family':'fixture','capture':'synthetic_fixture','name':'useful','path':'x.py','start_line':1,'end_line':2,'kind':'implementation','text':text.decode(),'observed_apis':[],'runtime_validation':'NOT_RUN'}
        self.source_result={'status':'CODE_INDEXED','source':self.source,'records':[self.record],'references':[],'gaps':[],'source_code_executed':False}
        self.calls=0
        self.v=patch.object(runner,'verify_base',return_value={'mocked_for_controller_test':True});self.v.start()
        self.validation=patch.object(bridge,'validate_questions',return_value=None);self.validation.start()
        with patch.object(runner,'inspect_source',return_value=self.source_result),patch.object(runner,'investigate',side_effect=self.search),patch.object(runner,'validate_questions',return_value=None):
            runner.run(self.m,self.src,self.work,self.forge)
        self.calls=0
        self.lookup=patch.object(bridge,'investigate',side_effect=self.search);self.lookup.start()
        self.session=fs.Supervisor(self.mp,self.src,self.work,self.forge)
    def tearDown(self):
        self.lookup.stop();self.validation.stop();self.v.stop();self.t.cleanup()
    def search(self,q,s,*a):
        self.calls+=1
        r=dict(self.record,snippet=self.record['text'],channels=['lexical'],matched_terms=['useful'],term_count=1)
        return {'id':q['id'],'query':q['query'],'intent':q['intent'],'status':'STATIC_LEADS_REVIEW_REQUIRED','results':[r],'references':[], 'lexical_results':[r],'forge_results':[], 'source_gaps':[{'source':'s','state':'CODE_INDEXED_WITH_GAPS','gaps':['review-fixture']}], 'warnings':['Constraint not proved.'],'next_action':'REVIEW_EVIDENCE','runtime_validation':'NOT_RUN','code_generated':False,'market_validated':False,'novelty_established':False}
    def request(self,rid='r1',query='useful'):
        return {'action':'ask','request_id':rid,'question':{'id':rid,'query':query,'intent':'code','sources':['s']}}
    def test_ask_uses_existing_context(self):
        out=self.session.execute(self.request());self.assertEqual(self.calls,1);self.assertEqual(out['result']['status'],'STATIC_LEADS_REVIEW_REQUIRED')
    def test_repeated_request_no_new_search(self):
        a=self.session.execute(self.request());b=self.session.execute(self.request());self.assertEqual(a['result'],b['result']);self.assertTrue(b['replayed']);self.assertEqual(self.calls,1)
    def test_repeated_id_different_query_refused(self):
        self.session.execute(self.request())
        with self.assertRaises(core.Blocked):self.session.execute(self.request(query='other'))
    def test_unknown_source_refused(self):
        q=self.request();q['question']['sources']=['outside']
        with self.assertRaises(core.Blocked):self.session.execute(q)
        self.assertEqual(self.calls,0)
    def test_initial_questions_consume_budget(self):
        self.session.execute(self.request('r1'));self.session.execute(self.request('r2'))
        with self.assertRaises(core.Blocked):self.session.execute(self.request('r3'))
        self.assertEqual(self.calls,2)
    def test_failure_consumes_slot(self):
        with patch.object(bridge,'investigate',side_effect=core.Blocked('FAILED')):self.assertEqual(self.session.execute(self.request())['state'],'FAILED')
        self.session.execute(self.request('r2'))
        with self.assertRaises(core.Blocked):self.session.execute(self.request('r3'))
    def test_failed_request_not_reexecuted(self):
        with patch.object(bridge,'investigate',side_effect=core.Blocked('FAILED')):self.session.execute(self.request())
        self.assertTrue(self.session.execute(self.request())['replayed']);self.assertEqual(self.calls,0)
    def test_interrupted_request_not_reexecuted(self):
        with patch.object(bridge,'investigate',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.session.execute(self.request())
        r=self.session.execute(self.request());self.assertEqual(r['state'],'INTERRUPTED_REQUIRES_REVIEW');self.assertEqual(self.calls,0)
    def test_cancel_prevents_new_search(self):
        self.session.execute({'action':'cancel'})
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
        self.assertEqual(self.calls,0)
    def test_unknown_action_rejected(self):
        with self.assertRaises(core.Blocked):self.session.execute({'action':'execute','command':'rm -rf /'})
    def test_permission_field_rejected(self):
        q=self.request();q['execute']=True
        with self.assertRaises(core.Blocked):self.session.execute(q)
    def test_source_mutation_blocks_replay(self):
        self.session.execute(self.request());(self.src/'x.py').write_text('changed')
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_report_mutation_refused(self):
        (self.work/'report.json').write_text('{}')
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_source_checkpoint_mutation_refused(self):
        p=core.Checkpoints(self.work/'checkpoints',core.digest(core.strict_loads((self.work/'binding.json').read_bytes()))).path('source:s');p.write_text('{}')
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_source_mutation_during_query_fails(self):
        def changed(*a):
            result=self.search(*a);(self.src/'x.py').write_text('changed');return result
        with patch.object(bridge,'investigate',side_effect=changed):out=self.session.execute(self.request())
        self.assertEqual(out['state'],'FAILED');self.assertIsNone(out['result'])
    def test_evidence_by_indexed_id(self):
        r=self.session.execute({'action':'inspect','request_id':'i1','source_id':'s','evidence_id':'definition1','max_chars':512})
        self.assertIn('return x',r['result']['text']);self.assertEqual(self.calls,0)
    def test_unknown_evidence_rejected(self):
        with self.assertRaises(core.Blocked):self.session.execute({'action':'inspect','request_id':'i1','source_id':'s','evidence_id':'../etc/passwd','max_chars':512})
    def test_inspection_capped(self):
        out=self.session.execute({'action':'inspect','request_id':'i1','source_id':'s','evidence_id':'definition1','max_chars':16})
        self.assertEqual(len(out['result']['text']),16);self.assertTrue(out['result']['truncated'])
    def test_bool_not_char_budget(self):
        with self.assertRaises(core.Blocked):self.session.execute({'action':'inspect','request_id':'i1','source_id':'s','evidence_id':'definition1','max_chars':True})
    def test_original_artifacts_unchanged(self):
        before={str(p):core.sha(p.read_bytes()) for p in self.work.rglob('*') if p.is_file() and p.name!='.runner.lock'}
        self.session.execute(self.request())
        self.assertTrue(all(core.sha(Path(p).read_bytes())==h for p,h in before.items()))
    def test_source_gaps_retained(self):
        r=self.session.execute(self.request())['result'];self.assertEqual(r['source_gaps'][0]['state'],'CODE_INDEXED_WITH_GAPS');self.assertTrue(r['warnings'])
    def test_no_execution_authority(self):
        r=self.session.execute(self.request());self.assertTrue(r['untrusted_content']);self.assertFalse(r['authority']['execute']);self.assertFalse(r['authority']['deploy'])
    def test_results_packet_bounded(self):
        r=self.session.execute(self.request());self.assertLessEqual(len(core.canonical(r)),4096)
    def test_status_has_remaining_budget(self):
        r=self.session.execute({'action':'status'});self.assertEqual(r['remaining_questions'],2);self.assertEqual(r['initial_questions'],1)
    def test_completed_mission_required(self):
        (self.work/'report.json').unlink()
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_concurrent_lock_rejected(self):
        with core.locked(self.work):
            with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_ledger_change_detected(self):
        self.session.execute(self.request())
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.work/'supervision'/'ledger.sqlite')) as db:
            db.execute("UPDATE requests SET result=?",('{}',));db.commit()
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_same_id_replay_in_new_session(self):
        a=self.session.execute(self.request());new=fs.Supervisor(self.mp,self.src,self.work,self.forge)
        b=new.execute(self.request());self.assertEqual(a['result'],b['result']);self.assertEqual(self.calls,1)

if __name__=='__main__':unittest.main()
