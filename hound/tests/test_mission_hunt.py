from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import hound
import atlas_hunt
import mission_hunt as mh

class MissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'source';self.src.mkdir();self.db=self.root/'db'
        raw=b'import trimesh\ndef f(a,b):\n trimesh.registration.icp(a,b,scale=False,reflection=False)\n return {"status":"unknown"}\n'
        (self.src/'a.py').write_bytes(raw)
        s={'source_id':'s','observation_id':'o','sha256':hound.sha(raw),'project_id':'p','relative_path':'a.py','display_path':'a.py'}
        self.card={'card_id':'c','function_id':'f','source_id':'s','observation_id':'o','project_id':'p','qualified_name':'f',
            'relative_path':'a.py','source_range':{'start':2,'end':4}}
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executescript('CREATE TABLE sources(id TEXT,current_observation TEXT,active INT,payload TEXT);CREATE TABLE cards(id TEXT,source_id TEXT,observation_id TEXT,payload TEXT);')
            c.execute('INSERT INTO sources VALUES(?,?,?,?)',('s','o',1,json.dumps(s)))
            c.execute('INSERT INTO cards VALUES(?,?,?,?)',('c','s','o',json.dumps(self.card)))
        with patch.object(atlas_hunt,'inspect_isolated',side_effect=lambda data,sid: hound.scan_bytes(data,source_id=sid)):
            self.r=atlas_hunt.hunt(self.db,self.src,{'s':{'path':'a.py'}},self.root/'hunt')
        self.m={'id':'test','title':'Known rigid call','requirements':[{'id':'r','profile_id':hound.RIGID,'statuses':['STATIC_CANDIDATE']}],
            'disqualifiers':[{'id':'d','profile_id':hound.RIGID,'statuses':['CONTRADICTED_CALL_CONTRACT']}],'exclude_tests':True}
    def tearDown(self):self.tmp.cleanup()
    def test_matching_goal(self):
        r=mh.query(self.m,self.r,self.db);self.assertEqual(r['eligible_candidate_count'],1);self.assertEqual(r['candidates'][0]['card_id'],'c')
    def test_no_production_approval(self):
        r=mh.query(self.m,self.r,self.db);self.assertFalse(r['candidates'][0]['release_approved'])
    def test_no_compatible_match_not_global_absence(self):
        self.m['requirements'][0]['statuses']=['NEEDS_CONTEXT'];r=mh.query(self.m,self.r,self.db)
        self.assertEqual(r['eligible_candidate_count'],0);self.assertTrue(r['no_match_is_not_proof_of_absence'])
    def test_same_function_contradiction_excludes(self):
        f=copy.deepcopy(next(x for x in self.r['findings'] if x['profile_id']==hound.RIGID));f['status']='CONTRADICTED_CALL_CONTRACT'
        f.pop('finding_id');f['finding_id']='finding_'+hound.sha(hound.canonical(f));self.r['findings'].append(f)
        r=mh.query(self.m,self.r,self.db);self.assertEqual(r['eligible_candidate_count'],0);self.assertEqual(len(r['excluded']),1)
    def test_catalog_mismatch(self):
        self.r['catalog_sha256']='a'*64
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_wrong_card_rejected(self):
        self.r['findings'][0]['card_id']='other'
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_changed_digest_rejected(self):
        self.r['findings'][0]['source_sha256']='b'*64
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_duplicate_finding_rejected(self):
        self.r['findings'].append(copy.deepcopy(self.r['findings'][0]))
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_invented_runtime_verification_rejected(self):
        self.r['findings'][0]['runtime_verified']=True
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_empty_goal_rejected(self):
        self.m['requirements']=[]
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_unknown_profile_rejected(self):
        self.m['requirements'][0]['profile_id']='fake'
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_uninspected_source_not_eligible(self):
        self.r['source_coverage'][0]['status']='NOT_INSPECTED'
        with self.assertRaises(ValueError):mh.query(self.m,self.r,self.db)
    def test_catalog_bytes_preserved(self):
        b=self.db.read_bytes();mh.query(self.m,self.r,self.db);self.assertEqual(b,self.db.read_bytes())
    def test_test_sources_filtered(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            s=json.loads(c.execute('SELECT payload FROM sources').fetchone()[0]);s['relative_path']='tests/test_a.py'
            c.execute('UPDATE sources SET payload=?',(json.dumps(s),))
        self.r['catalog_sha256']=hound.sha(self.db.read_bytes());r=mh.query(self.m,self.r,self.db);self.assertEqual(r['eligible_candidate_count'],0)
    def test_no_raw_code_in_dossier(self):
        self.assertNotIn('def f(',json.dumps(mh.query(self.m,self.r,self.db)))

class ContextTrailTests(unittest.TestCase):
    def setUp(self):
        self.card={'card_id':'a','project_id':'p','relative_path':'pkg/main.py','qualified_name':'caller',
          'module_imports':[{'alias':'fit','module':'helper','name':'fit','level':1}],
          'lexical_calls':[{'name':'fit','line':7}],'local_bindings':[]}
        self.other={'card_id':'b','project_id':'p','relative_path':'pkg/helper.py','qualified_name':'fit'}
    def test_relative_context_link(self):
        r=mh.context_trails(self.card,[self.card,self.other]);self.assertEqual(r[0]['target_card_ids'],['b'])
    def test_cross_project_not_linked(self):
        self.other['project_id']='different';self.assertEqual(mh.context_trails(self.card,[self.other]),[])
    def test_parameter_shadow_not_linked(self):
        self.card['local_bindings']=['fit'];self.assertEqual(mh.context_trails(self.card,[self.other]),[])
    def test_ambiguous_target_not_chosen(self):
        duplicate={**self.other,'card_id':'c'};r=mh.context_trails(self.card,[self.other,duplicate]);self.assertEqual(r[0]['status'],'AMBIGUOUS_CONTEXT_TARGET')
    def test_lexical_link_not_runtime_claim(self):
        r=mh.context_trails(self.card,[self.other]);self.assertEqual(r[0]['status'],'LEXICAL_CONTEXT_CANDIDATE_NOT_RUNTIME_EDGE')
