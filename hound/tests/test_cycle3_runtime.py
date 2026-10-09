"""Failure injection for source-hunt persistence; target source never executes."""
import json,threading,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor as RealPool
import atlas_hunt as ah,persistent_hunt as ph,hound
import test_persistent_hunt as fx

class RuntimeReview(unittest.TestCase):
 def setUp(self):self.f=fx.PersistentHuntTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 def test_pending_tasks_do_not_spend_retry_budget(self):
  observed=[]
  def inspect(data,sid):
   observed.append(json.loads((self.f.out/'attempts.json').read_text()))
   return hound.scan_bytes(data,source_id=sid)
  with patch.object(ph,'inspect_bounded',side_effect=inspect):self.f.run_job(workers=1)
  self.assertEqual(sum(observed[0].values()),1,'Queued but unstarted tasks must not be charged as attempts.')
  self.assertEqual(sum(observed[-1].values()),3)
 def test_cart_symlink_cannot_overwrite_other_file(self):
  self.f.out.mkdir();outside=self.f.root/'outside';outside.write_bytes(b'preserve')
  (self.f.out/'findings.ndjson.gz').symlink_to(outside)
  try:self.f.run_job()
  except ValueError:pass
  self.assertEqual(outside.read_bytes(),b'preserve')
 def test_empty_atlas_is_not_complete_in_legacy_runner(self):
  import sqlite3
  from contextlib import closing
  with closing(sqlite3.connect(self.f.db)) as c,c:c.execute('DELETE FROM cards');c.execute('DELETE FROM sources')
  with patch.object(ah,'inspect_isolated',side_effect=lambda d,s: hound.scan_bytes(d,source_id=s)):
   r=ah.hunt(self.f.db,self.f.src,{},self.f.out)
  self.assertNotEqual(r['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS')
 def test_malformed_retry_ledger_fails_cleanly(self):
  self.f.run_job(max_new_sources=0);(self.f.out/'attempts.json').write_text('[]')
  with self.assertRaises(ValueError):self.f.run_job()
 def test_boolean_retry_count_not_accepted(self):
  self.f.run_job(max_new_sources=0);(self.f.out/'attempts.json').write_text('{"source0":true}')
  with self.assertRaises(ValueError):self.f.run_job()
 def test_negative_retry_count_not_accepted(self):
  self.f.run_job(max_new_sources=0);(self.f.out/'attempts.json').write_text('{"source0":-5}')
  with self.assertRaises(ValueError):self.f.run_job()
 def test_unknown_source_in_retry_ledger_not_accepted(self):
  self.f.run_job(max_new_sources=0);(self.f.out/'attempts.json').write_text('{"outsider":1}')
  with self.assertRaises(ValueError):self.f.run_job()
 def test_malformed_checkpoint_finding_recomputes(self):
  self.f.run_job();p=next((self.f.out/'cache').glob('*.json'));box=json.loads(p.read_text())
  box['result']['findings']=[None];box['result_sha256']=hound.sha(hound.canonical(box['result']));p.write_text(json.dumps(box))
  r=self.f.run_job();self.assertEqual(r['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS')
 def test_checkpoint_list_root_recomputes(self):
  self.f.run_job();p=next((self.f.out/'cache').glob('*.json'));p.write_text('[]')
  r=self.f.run_job();self.assertEqual(r['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS')
 def test_duplicate_parser_findings_rejected(self):
  r=hound.scan_bytes(b'def f():\n return {"status":"unknown"}',source_id='s')
  r['findings'].append(dict(r['findings'][0]))
  with self.assertRaises(ValueError):ph.validate_result(r,'s',r['source_sha256'])
 def test_nonfinite_numeric_json_rejected(self):
  with self.assertRaises(ValueError):ah.strict_json('{"metric":1e999}')
 def test_missing_source_remains_explicit(self):
  (self.f.src/'f0.py').unlink();r=self.f.run_job();self.assertEqual(r['sources']['NOT_INSPECTED'],1)
 def test_no_parse_on_successful_second_run(self):
  self.f.run_job();self.f.parser.reset_mock();r=self.f.run_job();self.f.parser.assert_not_called()
  self.assertEqual(r['invocation']['cache_hits'],3)
