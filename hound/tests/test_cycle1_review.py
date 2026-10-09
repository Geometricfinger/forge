import json,unittest
from pathlib import Path
import hound
class Cycle1Review(unittest.TestCase): pass
def make(c):
 def test(self):
  r=hound.scan_bytes(c['source'].encode(),source_id='cycle1:'+c['id'])
  got=[f['status'] for f in r['findings'] if f['profile_id']==c['profile']]
  self.assertEqual(got,c['expected'],c['rationale'])
 return test
for c in json.loads(Path(__file__).with_name('cycle1_cases.json').read_text())['cases']:
 setattr(Cycle1Review,'test_'+c['id'],make(c))
