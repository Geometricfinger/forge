from pathlib import Path
import hashlib,json,sqlite3,tempfile,unittest
import hound
from atlas_hunt import hunt,load_catalog

class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.code=self.root/'sources';self.code.mkdir();self.db=self.root/'atlas.sqlite'
        self.raw=b'def example():\n    return {"status": "unknown"}\n';(self.code/'a.py').write_bytes(self.raw)
        self.obs='obs1';self.sid='source1'
        self.s={'source_id':self.sid,'observation_id':self.obs,'sha256':hound.sha(self.raw),'project_id':'p','display_path':'a.py','relative_path':'a.py'}
        self.c={'card_id':'card1','function_id':'fun1','source_id':self.sid,'observation_id':self.obs,'source_range':{'start':1,'end':2}}
        con=sqlite3.connect(self.db)
        con.executescript('CREATE TABLE sources(id TEXT,current_observation TEXT,active INT,payload TEXT);CREATE TABLE cards(id TEXT,source_id TEXT,observation_id TEXT,payload TEXT);')
        con.execute('INSERT INTO sources VALUES(?,?,?,?)',(self.sid,self.obs,1,json.dumps(self.s)))
        con.execute('INSERT INTO cards VALUES(?,?,?,?)',('card1',self.sid,self.obs,json.dumps(self.c)));con.commit();con.close()
        self.hash=hound.sha(self.db.read_bytes());self.loc={self.sid:{'path':'a.py'}}
    def tearDown(self):self.tmp.cleanup()
    def test_hunt_binds_existing_card(self):
        r=hunt(self.db,self.code,self.loc,self.root/'out')
        self.assertEqual(r['findings'][0]['card_id'],'card1');self.assertEqual(r['coverage'],'COMPLETE_FOR_SUPPLIED_ATLAS')
        self.assertEqual(hound.sha(self.db.read_bytes()),self.hash)
        self.assertEqual((self.code/'a.py').read_bytes(),self.raw)
        self.assertFalse(Path(str(self.db)+'-wal').exists());self.assertFalse(Path(str(self.db)+'-shm').exists())
    def test_missing_source_partial_not_absent_capability(self):
        r=hunt(self.db,self.code,{},self.root/'out')
        self.assertEqual(r['coverage'],'PARTIAL_FOR_SUPPLIED_ATLAS');self.assertEqual(r['sources'],{'SOURCE_NOT_SUPPLIED':1})
    def test_changed_source_not_promoted(self):
        (self.code/'a.py').write_bytes(self.raw+b'\n')
        r=hunt(self.db,self.code,self.loc,self.root/'out');self.assertEqual(r['finding_count'],0)
        self.assertEqual(r['source_coverage'][0]['reason'],'SOURCE_VERSION_MISMATCH')
    def test_existing_output_refused(self):
        out=self.root/'out';out.mkdir()
        with self.assertRaises(ValueError):hunt(self.db,self.code,self.loc,out)
    def test_unknown_card_range_refused(self):
        c=dict(self.c);c['source_range']={'start':9,'end':10}
        con=sqlite3.connect(self.db);con.execute('UPDATE cards SET payload=?',(json.dumps(c),));con.commit();con.close()
        r=hunt(self.db,self.code,self.loc,self.root/'out')
        self.assertEqual(r['source_coverage'][0]['reason'],'UNRESOLVED_ATLAS_CARD');self.assertEqual(r['finding_count'],0)
    def test_active_wal_refused(self):
        Path(str(self.db)+'-wal').write_bytes(b'')
        with self.assertRaises(ValueError):load_catalog(self.db)
    def test_profile_filter(self):
        r=hunt(self.db,self.code,self.loc,self.root/'out',profiles=[hound.SCALE])
        self.assertEqual(r['finding_count'],0);self.assertEqual(r['functions_inspected'],1)
    def test_unknown_profile_refused(self):
        with self.assertRaises(ValueError):hunt(self.db,self.code,self.loc,self.root/'out',profiles=['fake'])
    def test_output_inside_input_refused(self):
        with self.assertRaises(ValueError):hunt(self.db,self.code,self.loc,self.code/'out')
    def test_no_raw_body_in_output(self):
        hunt(self.db,self.code,self.loc,self.root/'out')
        self.assertNotIn('def example', (self.root/'out/hunt.json').read_text())
    def test_wrong_source_identity_rejected(self):
        with self.assertRaises(ValueError):hunt(self.db,self.code,{'bogus':{'path':'a.py'}},self.root/'out')
    def test_invalid_budget(self):
        with self.assertRaises(ValueError):hunt(self.db,self.code,self.loc,self.root/'out',max_sources=0)
