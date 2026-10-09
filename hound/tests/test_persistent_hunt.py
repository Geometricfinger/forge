from contextlib import closing
"""Persistence/permission tests use synthetic source; one real bounded parser check.
Scheduler tests substitute only subprocess transport with the same real parser.
"""
import ast
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import hound
import persistent_hunt as ph

class PersistentHuntTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.src=self.root/'sources';self.src.mkdir();self.db=self.root/'atlas.sqlite';self.out=self.root/'job';self.loc={}
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript('CREATE TABLE sources(id TEXT,current_observation TEXT,active INT,payload TEXT);CREATE TABLE cards(id TEXT,source_id TEXT,observation_id TEXT,payload TEXT);')
            for n in range(3):
                sid=f'source{n}';raw=f'def f{n}():\n return {{"status":"unknown"}}\n'.encode();name=f'f{n}.py'
                (self.src/name).write_bytes(raw);self.loc[sid]={'path':name}
                s={'source_id':sid,'observation_id':f'obs{n}','sha256':hound.sha(raw),'project_id':'project','relative_path':name,'display_path':name}
                card={'card_id':f'card{n}','function_id':f'func{n}','source_id':sid,'observation_id':f'obs{n}',
                    'qualified_name':f'f{n}','source_range':{'start':1,'end':2}}
                c.execute('INSERT INTO sources VALUES(?,?,?,?)',(sid,s['observation_id'],1,json.dumps(s)))
                c.execute('INSERT INTO cards VALUES(?,?,?,?)',(card['card_id'],sid,s['observation_id'],json.dumps(card)))
        self.transport=patch.object(ph,'inspect_bounded',side_effect=lambda data,sid: hound.scan_bytes(data,source_id=sid))
        self.parser=self.transport.start()
    def tearDown(self):self.transport.stop();self.temp.cleanup()
    def run_job(self,**kw):return ph.run(self.db,self.src,self.loc,self.out,**kw)
    def test_cold_all_complete(self):
        r=self.run_job();self.assertEqual(r['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS');self.assertEqual(r['finding_count'],3)
    def test_budget_then_resume(self):
        a=self.run_job(max_new_sources=1);self.assertEqual(a['sources']['DEFERRED_BY_SOURCE_BUDGET'],2)
        b=self.run_job(max_new_sources=2);self.assertEqual(b['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS');self.assertEqual(b['invocation']['cache_hits'],1)
    def test_repeated_run_no_new_parser_work(self):
        a=self.run_job();self.parser.reset_mock();b=self.run_job();self.parser.assert_not_called()
        self.assertEqual(a['findings'],b['findings']);self.assertEqual(b['invocation']['cache_hits'],3)
    def test_cached_source_still_verified(self):
        self.run_job();(self.src/'f0.py').write_text('changed')
        r=self.run_job();self.assertEqual(r['sources']['NOT_INSPECTED'],1);self.assertEqual(r['finding_count'],2)
    def test_corrupt_checkpoint_recomputed(self):
        self.run_job();next((self.out/'cache').glob('*.json')).write_text('{}');self.parser.reset_mock()
        r=self.run_job();self.assertEqual(self.parser.call_count,1);self.assertEqual(r['finding_count'],3)
    def test_partial_pending_file_not_a_checkpoint(self):
        self.run_job(max_new_sources=0);(self.out/'cache'/'abc.pending').write_text('{}')
        r=self.run_job();self.assertEqual(r['invocation']['parser_jobs_started'],3)
    def test_manifest_change_rejected(self):
        self.run_job()
        with self.assertRaisesRegex(ValueError,'JOB_BINDING_CHANGED'):self.run_job(profiles=[hound.ABSTAIN])
    def test_unknown_locator_rejected(self):
        self.loc['fake']={'path':'f0.py'}
        with self.assertRaisesRegex(ValueError,'LOCATOR_UNKNOWN_SOURCE'):self.run_job()
    def test_zero_budget_is_partial_not_success(self):
        r=self.run_job(max_new_sources=0);self.assertEqual(r['coverage'],'PARTIAL_FOR_SUPPLIED_ATLAS');self.assertEqual(r['functions_inspected'],0)
    def test_negative_budget_rejected(self):
        with self.assertRaises(ValueError):self.run_job(max_new_sources=-1)
    def test_workers_bounded(self):
        with self.assertRaises(ValueError):self.run_job(workers=0)
    def test_write_inside_source_rejected(self):
        self.out=self.src/'job'
        with self.assertRaises(ValueError):self.run_job()
    def test_source_and_atlas_unchanged(self):
        before={p.name:ph.hound.sha(p.read_bytes()) for p in self.src.iterdir()};db=self.db.read_bytes();self.run_job()
        self.assertEqual(self.db.read_bytes(),db);self.assertEqual(before,{p.name:ph.hound.sha(p.read_bytes()) for p in self.src.iterdir()})
    def test_no_original_source_in_checkpoints(self):
        self.run_job()
        self.assertTrue(all('def f' not in p.read_text() for p in (self.out/'cache').glob('*.json')))
    def test_cache_foreign_identity_rejected(self):
        self.run_job();p=next((self.out/'cache').glob('*.json'));box=json.loads(p.read_text());box['result']['source_id']='other'
        box['result_sha256']=hound.sha(hound.canonical(box['result']));p.write_text(json.dumps(box));self.parser.reset_mock()
        self.run_job();self.assertEqual(self.parser.call_count,1)
    def test_retry_budget_exhausted_not_pass(self):
        self.parser.side_effect=ValueError('TEST_FAILURE');self.run_job();self.run_job();self.parser.reset_mock();r=self.run_job()
        self.parser.assert_not_called();self.assertEqual(r['sources']['NOT_INSPECTED'],3)
    def test_one_parse_error_keeps_other_sources(self):
        def run(data,sid):
            if sid=='source1':raise ValueError('TEST_FAILURE')
            return hound.scan_bytes(data,source_id=sid)
        self.parser.side_effect=run;r=self.run_job();self.assertEqual(r['sources']['STATIC_INSPECTED'],2);self.assertEqual(len(list((self.out/'cache').glob('*.json'))),2)
    def test_lock_blocks_second_controller(self):
        with ph.exclusive_job(self.out):
            with self.assertRaisesRegex(ValueError,'JOB_BUSY'):self.run_job()
    def test_job_symlink_rejected(self):
        actual=self.root/'other';actual.mkdir();self.out.symlink_to(actual,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'SYMLINK'):self.run_job()
    def test_cache_symlink_rejected(self):
        self.out.mkdir();other=self.root/'other';other.mkdir();(self.out/'cache').symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'SYMLINK'):self.run_job()
    def test_snapshot_changed_after_read_refused(self):
        reader=ph.SnapshotReader(self.src);reader.raw('f0.py',10_000);(self.src/'f0.py').write_text('different')
        with self.assertRaises(ValueError):reader.verify_unchanged()
    def test_memoized_container_read_count(self):
        r=ph.SnapshotReader(self.src);r.raw('f0.py',10_000);r.raw('f0.py',10_000);self.assertEqual(r.read_bytes,len((self.src/'f0.py').read_bytes()))
    def test_invocations_are_preserved(self):
        self.run_job();self.run_job();self.assertEqual(len(list(self.out.glob('invocation-*.json'))),2)
    def test_cache_key_changes_with_engine_and_observation(self):
        s={'source_id':'s','observation_id':'o','sha256':'a'*64};a=ph.cache_key(s,'v1')
        self.assertNotEqual(a,ph.cache_key(s,'v2'));self.assertNotEqual(a,ph.cache_key({**s,'observation_id':'o2'},'v1'))

class RealParserTests(unittest.TestCase):
    def test_bounded_worker_real_not_target_execution(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'never-created'
            raw=f'open({str(p)!r},"w").write("bad")\ndef f():\n return {{"status":"unknown"}}\n'.encode()
            r=ph.inspect_bounded(raw,'actual-process');self.assertEqual(r['functions_inspected'],1);self.assertFalse(p.exists())
