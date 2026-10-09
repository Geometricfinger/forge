"""Frozen second review: record shape validation and cross-source selection."""
import copy, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from research_runner import bridge
from research_runner.core import Blocked, check_manifest, strict_loads
from test_contract import manifest

def candidate(source, number):
    return {'id':f'{source}-{number}','source_id':source,'source_sha256':'a'*64,
      'origin':'fixture://'+source,'capture':'synthetic_fixture','family':source,
      'name':f'match_{number}','path':source+'.py','start_line':number+1,'end_line':number+2,
      'kind':'implementation','text':'def match(): return 1','observed_apis':[],
      'runtime_validation':'NOT_RUN','test_path':False}

class SecondReview(unittest.TestCase):
    def test_scope_members_typed(self):
        m=manifest();m['questions'][0]['sources']=[{'unexpected':'map'}]
        with self.assertRaises(Blocked):check_manifest(m)
    def test_repository_field_typed(self):
        m=manifest();m['sources'][0].update(repository=[],commit='b'*40,repo_path='x.py',git_blob_sha1='c'*40)
        with self.assertRaises(Blocked):check_manifest(m)
    def test_hash_is_text(self):
        m=manifest();m['sources'][0]['sha256']=int('1'*64)
        with self.assertRaises(Blocked):check_manifest(m)
    def test_title_is_bounded_text(self):
        m=manifest();m['sources'][0]['title']={'claims':'any'}
        with self.assertRaises(Blocked):check_manifest(m)
    def test_title_size_bound(self):
        m=manifest();m['sources'][0]['title']='x'*2001
        with self.assertRaises(Blocked):check_manifest(m)
    def test_reference_filter_is_not_observed_api(self):
        m=manifest();m['questions'][0].update(intent='reference',required_api='x.y')
        with self.assertRaises(Blocked):check_manifest(m)
    def _run(self, reverse=False):
        groups=[]
        for source in ('a','b'):
            groups.append({'source':{'id':source},'status':'CODE_INDEXED','records':[candidate(source,n) for n in range(6)],
              'references':[],'gaps':[],'forge_report_path':'/'+source+'/corpus.json'})
        if reverse:groups.reverse()
        backend=SimpleNamespace(read_report=lambda p: str(p),
           search=lambda report,*a,**kw:{'results':[{'definition_id':report.strip('/')+'-'+str(i)} for i in range(6)]})
        q={'id':'q','query':'match','sources':['a','b'],'intent':'code'}
        with patch.object(bridge,'load_base',return_value=(backend,None,None)):
            return bridge.investigate(q,groups,Path('/unused'))
    def test_both_sources_visible_within_budget(self):
        self.assertEqual({r['source_id'] for r in self._run()['forge_results']},{'a','b'})
    def test_source_input_order_does_not_bias_forge_list(self):
        self.assertEqual(self._run()['forge_results'],self._run(True)['forge_results'])
    def test_lexical_full_annotations_are_indexed(self):
        r=candidate('a',0);r['text']='def invoke(cb: Callable):\n    return cb()'
        index=bridge.Lexical([r])
        try:self.assertEqual(index.search('Callable')[0]['id'],'a-0')
        finally:index.close()
    def test_reference_html_is_escaped(self):
        from research_runner.runner import render
        report={'objective':'<script>bad()</script>','status':'REFERENCE_ONLY', 'queries':[
          {'id':'q','query':'x','status':'REFERENCE_ONLY','results':[],'references':[
            {'kind':'research','title':'</h2><script>bad()</script>','text':'<img src=x onerror=bad()>'}], 'next_action':'review'}]}
        text=render(report)
        self.assertNotIn('<script>',text);self.assertNotIn('<img src=x',text);self.assertIn('&lt;script&gt;',text)

if __name__=='__main__':unittest.main()
