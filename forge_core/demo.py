"""Deterministic synthetic transport; invokes the real preserved Hound scanner."""
import base64
from .common import *
from .github import Response
from .policy import template
SAMPLE=b'import ast\n\ndef inspect_source(source):\n    tree = ast.parse(source)\n    return list(ast.walk(tree))\n'
COMMIT='1'*40;TREE='2'*40
class DemoGitHub:
    def get(self,p):
        k=p['kind'];blob=git_sha(SAMPLE)
        objects={
         'search':{'items':[{'full_name':'forge-fixture/syntax-example'}],'total_count':1,'incomplete_results':False},
         'repo':{'id':100,'full_name':'forge-fixture/syntax-example','private':False,'default_branch':'main','license':{'spdx_id':'MIT'}},
         'commit':{'sha':COMMIT,'commit':{'tree':{'sha':TREE}}},
         'tree':{'sha':TREE,'truncated':False,'tree':[{'path':'src/parse.py','type':'blob','mode':'100644','size':len(SAMPLE),'sha':blob}]},
         'file':{'type':'file','path':'src/parse.py','sha':blob,'size':len(SAMPLE),'encoding':'base64','content':base64.b64encode(SAMPLE).decode()}}
        return Response(200,canonical(objects[k]),{},'synthetic_fixture')

def run(workbench):
    m=template('understanding');m['title']='Synthetic workflow demonstration';m['public_brief']='Synthetic discovery; no public source is claimed.'
    mid=workbench.store.create_mission(m)
    result=workbench.discovery().run(mid,DemoGitHub(),steps=20,seconds=120)
    if result['status']!='COMPLETED_FOR_BOUNDED_MISSION' or len(result['candidates'])!=1 or len(result['candidates'][0]['findings'])!=2:raise Blocked('DEMO_ACCEPTANCE')
    return result
