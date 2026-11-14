"""Second review: shared objects, limits, and ledger paths."""
import copy, sqlite3, unittest
from pathlib import Path
from unittest.mock import patch
import test_supervisor as fixture
fs, core = fixture.fs, fixture.core

class SecondReview(unittest.TestCase):
    setUp=fixture.SupervisorContract.setUp
    tearDown=fixture.SupervisorContract.tearDown
    search=fixture.SupervisorContract.search
    request=fixture.SupervisorContract.request
    def test_caller_cannot_change_later_authority(self):
        out=self.session.execute(self.request())
        before=copy.deepcopy(fs.AUTHORITY)
        try:
            out['authority']['execute']=True
            self.assertFalse(self.session.execute({'action':'status'})['authority']['execute'])
        finally:fs.AUTHORITY.clear();fs.AUTHORITY.update(before)
    def test_runner_cancel_blocks_new_followup(self):
        (self.work/'CANCEL.json').write_text('{"canceled":true}')
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_evidence_reads_have_cap(self):
        for n in range(3):self.session.execute({'action':'inspect','request_id':f'i{n}','source_id':'s','evidence_id':'definition1','max_chars':64})
        with self.assertRaises(core.Blocked):self.session.execute({'action':'inspect','request_id':'i3','source_id':'s','evidence_id':'definition1','max_chars':64})
    def test_symlinked_ledger_refused(self):
        self.session.execute({'action':'status'})
        p=self.work/'supervision'/'ledger.sqlite';q=self.root/'old.sqlite';p.rename(q);p.symlink_to(q)
        with self.assertRaises(core.Blocked):self.session.execute(self.request())
    def test_no_permission_expansion_on_large_packet(self):
        old=self.search
        def big(*args):
            r=old(*args);r['warnings']=['x'*5000];return r
        with patch.object(self.session.bridge,'investigate',side_effect=big):out=self.session.execute(self.request())
        self.assertEqual(out['state'],'FAILED');self.assertEqual(out['reason'],'SUPERVISOR_RESPONSE_LIMIT');self.assertIsNone(out['result'])
    def test_original_id_cannot_be_reused(self):
        with self.assertRaises(core.Blocked):self.session.execute(self.request('q0'))
