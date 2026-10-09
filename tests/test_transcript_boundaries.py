import unittest
from forge_core.mixed_intake import inspect_container

class TranscriptBoundaries(unittest.TestCase):
    def test_incidental_filename_does_not_label_equation(self):
        data=b'The chain implemented in `geometry.py` is:\n\n```\nA = B x inverse(C)\n```\n'
        r=inspect_container(data,'notes.md');self.assertFalse(r['segments']);self.assertFalse(r['gaps']);self.assertEqual(r['inventory'][0]['status'],'NON_CODE_BLOCK')
    def test_numbered_filename_heading_keeps_code(self):
        data=b'3. pkg/main.py (core implementation)\n\n```\ndef lookup():\n    return 1\n```\n'
        r=inspect_container(data,'notes.txt');self.assertEqual(len(r['segments']),1);self.assertEqual(r['segments'][0]['logical_path_hint'],'pkg/main.py')
    def test_explicit_python_fence_needs_no_filename(self):
        r=inspect_container(b'See geometry.py for context.\n```python\ndef fit(): return 1\n```\n','notes.txt');self.assertEqual(len(r['segments']),1);self.assertIsNone(r['segments'][0]['logical_path_hint'])
    def test_old_heading_does_not_attach_to_later_block(self):
        data=b'## first.py\n```python\ndef first(): return 1\n```\n\nExplanation referencing other.py.\n```\nnot python !!!\n```\n'
        r=inspect_container(data,'notes.txt');self.assertEqual(len(r['segments']),1);self.assertFalse(r['gaps'])
