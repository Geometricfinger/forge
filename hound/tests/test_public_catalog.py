import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
import build_public_catalog as pc,atlas_hunt,hound
class PublicCatalog(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'src';self.src.mkdir();self.out=self.root/'out';self.raw=b'def helper(x):\n return x\n'
  (self.src/'module.py').write_bytes(self.raw);self.entry={'file':'module.py','repository':'sample/example','commit':'a'*40,'path':'pkg/module.py','source_id':'github:sample/example@'+'a'*40+':pkg/module.py','url':'https://github.com/sample/example/blob/'+'a'*40+'/pkg/module.py','sha256':hound.sha(self.raw),'git_blob_sha1':hashlib.sha1(b'blob '+str(len(self.raw)).encode()+b'\0'+self.raw).hexdigest()}
 def tearDown(self):self.tmp.cleanup()
 def run_capture(self):return pc.build(self.src,self.out,[self.entry])
 def test_real_metadata_schema(self):
  r=self.run_capture();sources,cards,_=atlas_hunt.load_catalog(self.out/'public_catalog.sqlite');self.assertEqual(len(sources),1);self.assertEqual(len(cards),1);self.assertFalse(r['source_bodies_retained'])
  self.assertNotIn(self.raw,(self.out/'public_catalog.sqlite').read_bytes())
 def test_unchanged_source(self):self.run_capture();self.assertEqual((self.src/'module.py').read_bytes(),self.raw)
 def test_changed_hash_rejected(self):
  self.entry['sha256']='0'*64
  with self.assertRaises(ValueError):self.run_capture()
 def test_blob_hash_rejected(self):
  self.entry['git_blob_sha1']='0'*40
  with self.assertRaises(ValueError):self.run_capture()
 def test_context_mismatch_rejected(self):
  self.entry['path']='other/module.py'
  with self.assertRaises(ValueError):self.run_capture()
 def test_empty_rejected(self):
  with self.assertRaises(ValueError):pc.build(self.src,self.out,[])
 def test_duplicate_rejected(self):
  with self.assertRaises(ValueError):pc.build(self.src,self.out,[self.entry,self.entry])
 def test_output_cannot_be_input(self):
  self.out=self.src/'out'
  with self.assertRaises(ValueError):self.run_capture()
