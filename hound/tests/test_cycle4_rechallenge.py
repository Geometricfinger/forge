"""Second semantic review after earlier fixes: nested writes and guard polarity."""
import unittest
import hound

class Rechallenge(unittest.TestCase):
 def scan(self,body,p,imports='import trimesh\n'):
  src=imports+'def f(a,b,flag):\n'+''.join('    '+x+'\n' for x in body.splitlines())
  return [f for f in hound.scan_bytes(src.encode(),source_id='rechallenge')['findings'] if f['profile_id']==p]
 def test_nested_nonlocal_write_invalidates_false(self):
  r=self.scan('s=False\ndef flip():\n    nonlocal s\n    s=True\nflip()\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',hound.RIGID)
  self.assertEqual(r[0]['status'],'NEEDS_CONTEXT')
 def test_nested_nonlocal_copy_rebinding(self):
  r=self.scan('m=a.copy()\ndef flip():\n    nonlocal m\n    m=a\nflip()\nm.apply_scale(2)',hound.SCALE)
  self.assertEqual(r[0]['status'],'UNRESOLVED_RECEIVER')
 def test_ordinary_closed_read_does_not_invalidate_bool(self):
  r=self.scan('s=False\ndef reader():\n    return s\nreader()\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',hound.RIGID)
  self.assertEqual(r[0]['status'],'STATIC_CANDIDATE')
 def test_negative_dimension_polarity_not_accepted(self):
  self.assertFalse(self.scan('if not (a.shape[1]!=3):\n    raise ValueError()',hound.DIMENSION))
 def test_flag_conditional_dimension_not_mandatory(self):
  self.assertFalse(self.scan('if a.shape[1]!=3 and flag:\n    raise ValueError()',hound.DIMENSION))
 def test_positive_or_dimension_remains(self):
  r=self.scan('if a.shape[1]!=3 or flag:\n    raise ValueError()',hound.DIMENSION)
  self.assertEqual(r[0]['expected_columns'],3)
 def test_dimension_raise_after_return_not_guard(self):
  self.assertFalse(self.scan('if a.shape[1]!=3:\n    return a\n    raise ValueError()',hound.DIMENSION))
 def test_path_raise_after_return_not_guard(self):
  self.assertFalse(self.scan('p=a.resolve()\nif not p.is_relative_to(b):\n    return p\n    raise ValueError()', 'storage.resolved_path_guard'))
 def test_conditional_escape_before_dimension_raise(self):
  self.assertFalse(self.scan('if a.shape[1]!=3:\n    if flag:\n        return a\n    raise ValueError()',hound.DIMENSION))
 def test_dead_else_after_literal_break_does_not_claim_call(self):
  self.assertFalse(self.scan('for x in [1]:\n    break\nelse:\n    trimesh.registration.icp(a,b,scale=False,reflection=False)',hound.RIGID))
 def test_break_keeps_following_code_reachable(self):
  r=self.scan('s=False\nfor x in [1]:\n    break\nelse:\n    s=True\ntrimesh.registration.icp(a,b,scale=s,reflection=False)',hound.RIGID)
  self.assertEqual(r[0]['status'],'STATIC_CANDIDATE')
