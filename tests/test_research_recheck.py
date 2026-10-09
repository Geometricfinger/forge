"""Second-review counterexamples, frozen before their corresponding repairs."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from forge_core import corpus
from test_research_retrieval import report_from
from test_dependency_context import archive

class Recheck(unittest.TestCase):
    def ctx(self,items):
        r=report_from([('p.zip',archive(items))]);row=next(x for x in corpus.rows(r) if x['name'].endswith('use'));return corpus.dependency_context(r,row['definition_id'])
    def test_relative_import_cannot_walk_above_snapshot_root(self):
        c=self.ctx([('pkg/a.py','from ....b import h\ndef use(): return h()\n'),('b.py','def h():return 1\n')]);self.assertEqual(c['references'][0]['candidates'],[])
    def test_self_must_be_a_parameter(self):
        c=self.ctx([('x.py','class A:\n    def h(self):return 1\n    def use(obj):return self.h()\n')]);self.assertEqual(c['references'][0]['candidates'],[])
    def test_from_package_submodule_alias(self):
        c=self.ctx([('pkg/a.py','from pkg import b as target\ndef use():return target.h()\n'),('pkg/b.py','def h():return 1\n')]);self.assertEqual([x['name'] for x in c['references'][0]['candidates']],['h'])
    def test_exported_class_and_submodule_remain_ambiguous(self):
        c=self.ctx([('a.py','from pkg import B\ndef use():return B.h()\n'),('pkg/__init__.py','class B:\n    def h():return 1\n'),('pkg/B.py','def h():return 2\n')]);self.assertEqual(len(c['references'][0]['candidates']),2);self.assertEqual(c['references'][0]['status'],'AMBIGUOUS_CANDIDATES')
    def test_concurrent_snapshot_queries(self):
        reports=[report_from([('x.py','def value'+str(i)+'():return 1\n')]) for i in range(12)]
        def job(i):
            r=reports[i%12];q=corpus.search(r,'value'+str(i%12));return q['results'][0]['name']
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(job,range(120)))
        self.assertEqual(results,['value'+str(i%12) for i in range(120)])
    def test_bm25_tie_uses_document_length(self):
        r=report_from([('a.py','def a():\n    """quartz '+('noise '*300)+'"""\n    return 1\ndef z():\n    """quartz"""\n    return 1\n')]);q=corpus.search(r,'quartz');self.assertEqual(q['results'][0]['name'],'z')

if __name__=='__main__':unittest.main()

class LegacyCompatibility(unittest.TestCase):
    def legacy(self):
        from test_research_retrieval import report_from
        report = report_from([('archive.py', 'def z_long():\n    """alpha beta alpha beta gamma delta epsilon zeta eta theta iota kappa"""\n    return 1\n\ndef a_short():\n    """alpha beta"""\n    return 2\n')])
        for item in report['sources']:
            for segment in item['segments']:
                segment.pop('module_structure', None)
                for definition in segment['definitions']:
                    definition.pop('structure', None)
        return report

    def test_old_snapshot_explicitly_uses_compatible_order(self):
        from forge_core import corpus
        report = self.legacy()
        old = corpus.search_legacy(report, 'alpha beta')
        new = corpus.search(report, 'alpha beta')
        self.assertEqual([r['definition_id'] for r in new['results']], [r['definition_id'] for r in old['results']])
        self.assertEqual(new['ranker_version'], 'legacy-coverage-compatible-1')
        self.assertIn('REINDEX_REQUIRED_FOR_STRUCTURED_RANKING', new['warnings'])

    def test_old_snapshot_filters_still_require_observations(self):
        from forge_core import corpus
        report = self.legacy()
        result = corpus.search(report, 'alpha', filters={'required_apis': ['os.replace']})
        self.assertEqual(result['results'], [])
        self.assertEqual(result['ranker_version'], 'legacy-coverage-compatible-1')

    def test_new_snapshot_still_uses_research_ranker(self):
        from forge_core import corpus
        from test_research_retrieval import report_from
        result = corpus.search(report_from([('x.py', 'def hash_value():\n    return 1\n')]), 'hash')
        self.assertEqual(result['ranker_version'], 'field-bm25-rrf-1')
        self.assertNotIn('REINDEX_REQUIRED_FOR_STRUCTURED_RANKING', result['warnings'])
