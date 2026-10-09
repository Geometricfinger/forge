from contextlib import closing
"""Regressions for a mismatch found in the freshly read Drive archive.
Synthetic code is parsed only. Full integration uses a disposable Atlas.
"""
import ast,copy,json,sqlite3,tempfile,unittest
from pathlib import Path
import hound
from atlas_hunt import resolve_atlas_card,hunt

RAW=b'class Example:\n    @classmethod\n    def decide(cls):\n        return {"status": "ambiguous"}\n'

def case(raw=RAW,name='Example.decide'):
    finding=next(f for f in hound.scan_bytes(raw,source_id='source1')['findings'] if f['qualified_name']==name)
    node=next(n for q,n in hound.base.functions(ast.parse(raw)) if q==name)
    card={'card_id':'existing-card','function_id':'existing-function','source_id':'source1','observation_id':'obs1',
          'qualified_name':name,'source_range':{'start':min([node.lineno]+[x.lineno for x in node.decorator_list]),'end':node.end_lineno}}
    return finding,card

class DecoratorBindingTests(unittest.TestCase):
    def test_classmethod_preserves_ids(self):
        f,c=case();self.assertIs(resolve_atlas_card(RAW,f,[c]),c)
    def test_stacked_decorators(self):
        raw=b'@outer\n@inner\ndef pick():\n    return {"status": "unknown"}\n';f,c=case(raw,'pick')
        self.assertEqual(resolve_atlas_card(raw,f,[c])['source_range']['start'],1)
    def test_multiline_decorator(self):
        raw=b'@deco(\n    "value"\n)\ndef pick():\n    return {"status": "unknown"}\n';f,c=case(raw,'pick')
        self.assertIs(resolve_atlas_card(raw,f,[c]),c)
    def test_async_staticmethod(self):
        raw=b'class E:\n    @staticmethod\n    async def pick():\n        return {"status": "unknown"}\n';f,c=case(raw,'E.pick')
        self.assertIs(resolve_atlas_card(raw,f,[c]),c)
    def test_nested_qualified_name(self):
        raw=b'def outer():\n    @deco\n    def inner():\n        return {"status": "unknown"}\n';f,c=case(raw,'outer.<locals>.inner')
        self.assertIs(resolve_atlas_card(raw,f,[c]),c)
    def test_def_start_legacy(self):
        f,c=case();c['source_range']['start']=f['function_lines'][0];c.pop('qualified_name')
        self.assertIs(resolve_atlas_card(RAW,f,[c]),c)
    def test_missing_qualified_name_refused_for_decorator(self):
        f,c=case();c.pop('qualified_name')
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c])
    def test_wrong_name_refused(self):
        f,c=case();c['qualified_name']='Different.decide'
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c])
    def test_leading_comment_not_accepted(self):
        raw=b'# context\n'+RAW;f,c=case(raw);c['source_range']['start']=1
        with self.assertRaises(ValueError):resolve_atlas_card(raw,f,[c])
    def test_second_of_stacked_decorators_refused(self):
        raw=b'@outer\n@inner\ndef pick():\n    return {"status": "unknown"}\n';f,c=case(raw,'pick');c['source_range']['start']=2
        with self.assertRaises(ValueError):resolve_atlas_card(raw,f,[c])
    def test_wrong_end_refused(self):
        f,c=case();c['source_range']['end']+=1
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c])
    def test_duplicate_candidates_refused(self):
        f,c=case()
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c,copy.deepcopy(c)])
    def test_changed_bytes_refused(self):
        f,c=case()
        with self.assertRaises(ValueError):resolve_atlas_card(RAW+b'\n',f,[c])
    def test_wrong_source_refused(self):
        f,c=case();c['source_id']='source2'
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c])
    def test_forged_definition_range_refused(self):
        f,c=case();f['function_lines'][0]+=1
        with self.assertRaises(ValueError):resolve_atlas_card(RAW,f,[c])
    def test_full_hunt_binds_decorated_card(self):
        f,c=case()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'sources';src.mkdir();(src/'example.py').write_bytes(RAW);db=root/'atlas.sqlite'
            s={'source_id':'source1','observation_id':'obs1','project_id':'p','sha256':hound.sha(RAW),'display_path':'example.py','relative_path':'example.py'}
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript('CREATE TABLE sources(id TEXT,current_observation TEXT,active INT,payload TEXT);CREATE TABLE cards(id TEXT,source_id TEXT,observation_id TEXT,payload TEXT);')
                con.execute('INSERT INTO sources VALUES(?,?,?,?)',('source1','obs1',1,json.dumps(s)))
                con.execute('INSERT INTO cards VALUES(?,?,?,?)',('existing-card','source1','obs1',json.dumps(c)))
            before=hound.sha(db.read_bytes())
            r=hunt(db,src,{'source1':{'path':'example.py'}},root/'out')
            self.assertEqual(r['sources'],{'STATIC_INSPECTED':1});self.assertEqual(r['finding_count'],1)
            self.assertEqual(r['findings'][0]['card_id'],'existing-card');self.assertEqual(hound.sha(db.read_bytes()),before)
            self.assertEqual((src/'example.py').read_bytes(),RAW)
