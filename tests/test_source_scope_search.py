import unittest
from forge_core import corpus
import test_corpus as helper

class SourceScopeSearch(unittest.TestCase):
    setUp=helper.CorpusTests.setUp;tearDown=helper.CorpusTests.tearDown;make=helper.CorpusTests.make;runit=helper.CorpusTests.runit
    def test_script_search_has_no_fake_function(self):
        self.files=[('scratch.txt',b'import hashlib\nvalue = hashlib.sha256(b"a")\n')];self.make();r=self.runit();q=corpus.search(r,'hashlib')
        self.assertEqual(len(q['results']),1);row=q['results'][0];self.assertEqual(row['record_kind'],'source_scope');self.assertNotIn('definition_id',row);self.assertNotIn('card_id',row);self.assertEqual(r['summary']['definitions'],0)
    def test_source_import_is_not_executed_api(self):
        self.files=[('scratch.py',b'import hashlib\nvalue = 1\n')];self.make();r=self.runit();q=corpus.search(r,'hashlib');self.assertEqual(q['results'][0]['observed_apis'],[]);self.assertEqual(q['results'][0]['declared_imports'],['hashlib'])
    def test_source_scope_test_filter(self):
        self.files=[('tests/test_scratch.py',b'import hashlib\nvalue = 1\n')];self.make();r=self.runit();self.assertEqual(len(corpus.search(r,'hashlib')['results']),0);self.assertEqual(len(corpus.search(r,'hashlib',include_tests=True)['results']),1)
    def test_functions_remain_distinct(self):
        r=self.runit();q=corpus.search(r,'digest');self.assertTrue(all(x['record_kind']=='function' for x in q['results']));self.assertEqual(r['summary']['source_scope_records'],0)
