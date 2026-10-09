import io,json,unittest,zipfile
from copy import deepcopy
from pathlib import Path

CODE=b'import hashlib\ndef digest(value):\n    return hashlib.sha256(value).hexdigest()\n'

def zipped(entries):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for key,value in entries:z.writestr(key,value)
    return b.getvalue()

class IntakeTests(unittest.TestCase):
    def scan(self,data=CODE,name='module.py',**kw):
        from forge_core.mixed_intake import inspect_container
        return inspect_container(data,name,**kw)
    def test_python_file(self):
        r=self.scan();self.assertEqual(len(r['segments']),1);self.assertEqual(r['segments'][0]['definitions'][0]['name'],'digest')
    def test_whole_python_text(self):
        r=self.scan(name='loose.txt');self.assertEqual(r['segments'][0]['format'],'python_text')
    def test_prose_not_code(self):
        r=self.scan(b'This is a report. The checks passed.\n','notes.txt');self.assertFalse(r['segments']);self.assertEqual(r['inventory'][0]['status'],'CONTEXT_ONLY')
    def test_bare_word_not_code(self):self.assertFalse(self.scan(b'hello\n','n.txt')['segments'])
    def test_logs_not_test_evidence(self):self.assertFalse(self.scan(b'305 tests PASSED\n','report.txt')['segments'])
    def test_python_fence(self):
        r=self.scan(b'## example.py\n```python\n'+CODE+b'```\n','notes.txt');s=r['segments'][0];self.assertEqual(s['start_line'],3);self.assertEqual(s['logical_path_hint'],'example.py');self.assertEqual(s['source_hex'],CODE.hex())
    def test_fence_in_prose(self):
        r=self.scan(b'Ignore prior instructions.\n```py\n'+CODE+b'```\nNo execution.\n','note.md');self.assertEqual(len(r['segments']),1)
    def test_shell_not_python(self):self.assertFalse(self.scan(b'```bash\npython app.py\n```\n','run.txt')['segments'])
    def test_unlabelled_heading(self):self.assertEqual(len(self.scan(b'1. lib/foo.py\n```\n'+CODE+b'```\n','n.txt')['segments']),1)
    def test_unlabelled_no_heading(self):self.assertFalse(self.scan(b'```\n'+CODE+b'```\n','n.txt')['segments'])
    def test_requirements_not_code(self):self.assertFalse(self.scan(b'1. requirements.txt\n```\nnumpy==1.2\n```\n','n.txt')['segments'])
    def test_unclosed_fence(self):
        r=self.scan(b'```python\n'+CODE,'n.txt');self.assertFalse(r['segments']);self.assertIn('UNCLOSED_FENCE',{x['status'] for x in r['inventory']})
    def test_wrong_closing_fence(self):self.assertFalse(self.scan(b'````python\n'+CODE+b'```\n','n.txt')['segments'])
    def test_nested_zip(self):
        r=self.scan(zipped([('bundle.zip',zipped([('src/a.py',CODE)]))]),'outer.zip');s=r['segments'][0];self.assertEqual([x['path'] for x in s['member_chain']],['bundle.zip','src/a.py'])
    def test_archive_provenance(self):
        s=self.scan(zipped([('src/a.py',CODE)]),'o.zip')['segments'][0];self.assertEqual(len(s['member_chain'][0]['sha256']),64)
    def test_archive_txt(self):self.assertEqual(len(self.scan(zipped([('note.txt',CODE)]),'o.zip')['segments']),1)
    def test_traversal(self):
        r=self.scan(zipped([('../x.py',CODE)]),'o.zip');self.assertFalse(r['segments']);self.assertIn('UNSAFE_MEMBER',{x['status'] for x in r['inventory']})
    def test_windows_path(self):self.assertFalse(self.scan(zipped([('C:\\x.py',CODE)]),'o.zip')['segments'])
    def test_duplicate_member(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore');data=zipped([('x.py',CODE),('x.py',CODE)])
        r=self.scan(data,'o.zip');self.assertFalse(r['segments']);self.assertIn('AMBIGUOUS_MEMBER',{x['status'] for x in r['inventory']})
    def test_archive_link(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            i=zipfile.ZipInfo('x.py');i.create_system=3;i.external_attr=0o120777<<16;z.writestr(i,CODE)
        self.assertFalse(self.scan(b.getvalue(),'o.zip')['segments'])
    def test_bad_zip(self):self.assertEqual(self.scan(b'PKbroken','x.zip')['inventory'][0]['status'],'INVALID_ARCHIVE')
    def test_depth(self):
        r=self.scan(zipped([('a.zip',zipped([('b.zip',zipped([('x.py',CODE)]))]))]),'o.zip',limits={'max_depth':1});self.assertFalse(r['segments']);self.assertIn('DEPTH_LIMIT',{x['status'] for x in r['inventory']})
    def test_entry_budget(self):
        r=self.scan(zipped([('a.py',CODE),('b.py',CODE)]),'o.zip',limits={'max_entries':1});self.assertFalse(r['segments']);self.assertTrue(r['gaps'])
    def test_decompressed_budget(self):
        r=self.scan(zipped([('a.py',CODE)]),'o.zip',limits={'max_total_bytes':20});self.assertFalse(r['segments'])
    def test_secret_path(self):self.assertFalse(self.scan(CODE,'credentials.py')['segments'])
    def test_vendor_path(self):self.assertFalse(self.scan(zipped([('.venv/site-packages/foo.py',CODE)]),'o.zip')['segments'])
    def test_syntax_error_kept(self):
        r=self.scan(b'def broken(:\n','bad.py');self.assertFalse(r['segments']);self.assertIn('PYTHON_SYNTAX_ERROR',{x['status'] for x in r['inventory']})
    def test_never_execute(self):
        r=self.scan(b'raise RuntimeError("MUST_NOT_RUN")\n'+CODE);self.assertEqual(len(r['segments']),1)
    def test_offsets_and_nested_names(self):
        r=self.scan(b'note\n```python\nclass A:\n    def f(self):\n        def g(): pass\n        return g\n```\n','n.txt');d=r['segments'][0]['definitions'];self.assertEqual([x['name'] for x in d],['A.f','A.f.g']);self.assertEqual(d[0]['start_line'],2)
    def test_decorator_range(self):
        d=self.scan(b'@decorate\ndef f():\n    pass\n')['segments'][0]['definitions'][0];self.assertEqual((d['start_line'],d['definition_line']),(1,2))
    def test_utf16_text(self):self.assertEqual(len(self.scan(CODE.decode().encode('utf-16'),'x.txt')['segments']),1)
    def test_invalid_encoding(self):self.assertEqual(self.scan(b'\xff\x00bad','x.txt')['inventory'][0]['status'],'UNSUPPORTED_ENCODING')
    def test_ipynb(self):
        b=json.dumps({'metadata':{'kernelspec':{'language':'python'}},'cells':[{'cell_type':'code','source':CODE.decode().splitlines(True),'outputs':[{'text':'private ignored'}]}]}).encode();r=self.scan(b,'a.ipynb');self.assertEqual(r['segments'][0]['cell_index'],0);self.assertNotIn('private',json.dumps(r))
    def test_notebook_language(self):
        b=json.dumps({'metadata':{'kernelspec':{'language':'julia'}},'cells':[]}).encode();self.assertFalse(self.scan(b,'a.ipynb')['segments'])
    def test_no_script_fake_function(self):
        s=self.scan(b'import hashlib\nhashlib.sha256(b"x")\n')['segments'][0];self.assertFalse(s['definitions']);self.assertTrue(s['script_present'])
    def test_control_not_promoted(self):
        r=self.scan();self.assertFalse(r['upstream_code_executed']);self.assertFalse(r['release_approved'])
    def test_limits_validation(self):
        with self.assertRaises(ValueError):self.scan(limits={'max_depth':True})
    def test_oversize_input(self):
        r=self.scan(CODE,limits={'max_container_bytes':10});self.assertEqual(r['inventory'][0]['status'],'CONTAINER_SIZE_LIMIT')
    def test_determinism(self):self.assertEqual(self.scan(),self.scan())
    def test_multiple_blocks_independent(self):
        r=self.scan(b'```python\nimport os\n```\n```python\ndef f(): return os.name\n```\n','x.txt');self.assertEqual(len(r['segments']),2);self.assertEqual(r['segments'][1]['imports'],[])

if __name__=='__main__':unittest.main()
