"""API observations belong to the exact inspected definition, not its spelling."""
import tempfile
from pathlib import Path
import unittest

from forge_core import corpus
from forge_core.common import canonical
from forge_core.workspace import initialize
from test_research_retrieval import report_from


PROFILE={'schema':1,'id':'scope-fixture','title':'Synthetic scope observations',
         'targets':[{'api':'hashlib.sha256','capability':'digest'}]}


class CorpusApiAttributionTests(unittest.TestCase):
    def real_scan(self, code):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        base=Path(temporary.name).resolve();source=base/'source';source.mkdir()
        (source/'fixture.py').write_text(code)
        manifest=base/'manifest.json'
        manifest.write_bytes(canonical({'schema':1,'files':corpus.local_rows(source)}))
        work=initialize(base/'home')
        report=corpus.run(work,source,manifest,work.home/'corpora'/'fixture',PROFILE)
        self.assertEqual(report['status'],'COMPLETED_FOR_SELECTED_CONTAINERS')
        return report

    def test_repeated_function_observation_stays_with_exact_definition(self):
        report=self.real_scan('import hashlib\ndef digest(value):\n    return hashlib.sha256(value)\n'
                              'def digest(value):\n    return value\n')
        rows=list(corpus.rows(report))
        self.assertEqual([(r['local_start_line'],r['observed_apis']) for r in rows],
                         [(2,['hashlib.sha256']),(4,[])])
        found=corpus.search(report,'digest',filters={'required_apis':['hashlib.sha256']})
        self.assertEqual([r['definition_id'] for r in found['results']],[rows[0]['definition_id']])

    def test_nested_global_observation_is_retained_without_parent_promotion(self):
        report=self.real_scan('import hashlib\ndef factory():\n    class Worker:\n'
                              '        def digest(self, value):\n            return hashlib.sha256(value)\n'
                              '    return Worker\n')
        self.assertEqual([(r['name'],r['observed_apis']) for r in corpus.rows(report)],
                         [('factory',[]),('factory.Worker.digest',['hashlib.sha256'])])

    def test_decorator_start_is_not_the_observed_definition_line(self):
        report=self.real_scan('import hashlib\n@marker\nasync def digest(value):\n'
                              '    return hashlib.sha256(value)\n@marker\n'
                              'async def digest(value):\n    return value\n')
        self.assertEqual([(r['local_start_line'],r['observed_apis']) for r in corpus.rows(report)],
                         [(2,['hashlib.sha256']),(5,[])])

    def test_repeated_class_method_scopes_remain_separate(self):
        report=self.real_scan('import hashlib\nclass Worker:\n    def digest(self,value):\n'
                              '        return hashlib.sha256(value)\nclass Worker:\n'
                              '    def digest(self,value):\n        return value\n')
        self.assertEqual([r['observed_apis'] for r in corpus.rows(report)],[['hashlib.sha256'],[]])

    def test_legacy_unranged_observation_requires_unique_name(self):
        for code,expected in [('def digest(x): return x\n',[['hashlib.sha256']]),
                              ('def digest(x): return x\ndef digest(x): return x\n',[[],[]])]:
            with self.subTest(code=code):
                report=report_from([('fixture.py',code)])
                report['sources'][0]['segments'][0]['hound']['findings']=[
                    {'qualified_name':'digest','resolved_api':'hashlib.sha256'}]
                self.assertEqual([r['observed_apis'] for r in corpus.rows(report)],expected)

    def test_malformed_or_wrong_ranges_cannot_fall_back_to_name(self):
        for span in (None,[],[1],[True,1],[1,2],[2,2],['1',1]):
            with self.subTest(span=span):
                report=report_from([('fixture.py','def digest(x): return x\n')])
                report['sources'][0]['segments'][0]['hound']['findings']=[
                    {'qualified_name':'digest','function_lines':span,'resolved_api':'hashlib.sha256'}]
                self.assertEqual(next(corpus.rows(report))['observed_apis'],[])

    def test_legacy_function_name_fields_still_match_unique_scopes(self):
        for field in ('function','function_name'):
            with self.subTest(field=field):
                report=report_from([('fixture.py','def digest(x): return x\n')])
                report['sources'][0]['segments'][0]['hound']['findings']=[
                    {field:'digest','api':'hashlib.sha256'}]
                self.assertEqual(next(corpus.rows(report))['observed_apis'],['hashlib.sha256'])

    def test_module_observations_do_not_acquire_function_authority(self):
        report=self.real_scan('import hashlib\nx=hashlib.sha256(b"synthetic")\n'
                              'def digest(value):\n    return value\n')
        self.assertEqual(next(corpus.rows(report))['observed_apis'],[])
        self.assertEqual(next(corpus.source_rows(report))['observed_apis'],['hashlib.sha256'])

    def test_nested_legacy_name_and_duplicate_api_records(self):
        report=report_from([('fixture.py','def outer():\n    def inner(x): return x\n    return inner\n')])
        report['sources'][0]['segments'][0]['hound']['findings']=[
            {'qualified_name':'outer.<locals>.inner','function_lines':[2,2],'resolved_api':'hashlib.sha256'},
            {'qualified_name':'outer.<locals>.inner','function_lines':[2,2],'resolved_api':'hashlib.sha256'}]
        self.assertEqual([r['observed_apis'] for r in corpus.rows(report)],[[],['hashlib.sha256']])


if __name__=='__main__':unittest.main()
