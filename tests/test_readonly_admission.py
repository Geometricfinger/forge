"""Rejected CLI inputs must not initialize a workspace inside source material."""
import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def inventory(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

class ReadOnlyAdmission(unittest.TestCase):
    def invoke(self,home,source):
        p=subprocess.run([sys.executable,str(ROOT/'forge.py'),'--home',str(home),'corpus-run',
                          '--source-root',str(source),'--run-id','example'],capture_output=True,text=True,timeout=60)
        self.assertEqual(p.returncode,2,p.stdout+p.stderr);return json.loads(p.stdout)
    def test_child_home_does_not_touch_source(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();source=root/'source';source.mkdir();(source/'main.py').write_text('pass\n')
            before=inventory(root);result=self.invoke(source/'workspace',source)
            self.assertEqual(result['reason'],'CORPUS_SEPARATE_OUTPUT');self.assertEqual(inventory(root),before)
            self.assertFalse((source/'workspace').exists())
    def test_same_home_does_not_touch_source(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t).resolve();(source/'main.py').write_text('pass\n');before=inventory(source)
            self.assertEqual(self.invoke(source,source)['reason'],'CORPUS_SEPARATE_OUTPUT')
            self.assertEqual(inventory(source),before)
    def test_source_inside_home_does_not_initialize_home(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();home=root/'home';source=home/'source';source.mkdir(parents=True)
            (source/'main.py').write_text('pass\n');before=inventory(root)
            self.assertEqual(self.invoke(home,source)['reason'],'CORPUS_SEPARATE_OUTPUT');self.assertEqual(inventory(root),before)
    def test_missing_source_does_not_initialize_home(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();home=root/'new-home'
            self.assertEqual(self.invoke(home,root/'absent')['reason'],'SOURCE_ROOT');self.assertFalse(home.exists())
    def test_linked_source_does_not_initialize_home(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t).resolve();(root/'actual').mkdir();(root/'link').symlink_to(root/'actual',target_is_directory=True)
            self.assertEqual(self.invoke(root/'new-home',root/'link')['reason'],'SYMLINK_PATH')
            self.assertFalse((root/'new-home').exists())
