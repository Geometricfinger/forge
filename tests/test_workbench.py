import base64,copy,hashlib,http.client,json,os,sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from forge_core.common import *
from forge_core import policy
from forge_core.store import Store
from forge_core.discovery import Discovery
from forge_core.github import Response,GitHub,delay_from
from forge_core.demo import DemoGitHub,SAMPLE,TREE,COMMIT
from forge_core.report import render

class Clock:
    def __init__(self):self.value=10000.
    def __call__(self):return self.value
    def advance(self,n):self.value+=n
class StubAnalyzer:
    def __init__(self):self.calls=0
    def scan(self,data,sid,p):
        self.calls+=1
        return {'source_id':sid,'source_sha256':sha(data),'registry_sha256':sha(canonical(p)),'functions_inspected':1,'findings':[{'resolved_api':'ast.parse','capability':'syntax','qualified_name':'f'}]}
class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.clock=Clock()
        self.store=Store.create(self.root/'home',{},self.clock);self.spec=policy.template('understanding');self.mid=self.store.create_mission(self.spec)
        self.analyzer=StubAnalyzer();self.disc=Discovery(self.store,self.analyzer);self.transport=DemoGitHub()
    def tearDown(self):self.tmp.cleanup()
    def step(self,obj=None,status=200,headers=None):
        p=self.store.claim(self.mid);r=self.transport.get(p) if obj is None else Response(status,canonical(obj),headers or {},'synthetic_fixture')
        self.disc.accept(p,r);return p,r
    def to_file(self):
        for _ in range(4):self.step()
        return self.store.claim(self.mid)

class PolicyTests(unittest.TestCase):
    def test_templates_valid(self):
        for n in ('understanding','reliability','evaluation'):policy.validate(policy.template(n))
    def test_bad_template(self):
        with self.assertRaises(Blocked):policy.template('../secret')
    def test_extra_field(self):
        m=policy.template('understanding');m['command']='rm -rf .'
        with self.assertRaises(Blocked):policy.validate(m)
    def test_boolean_limit(self):
        m=policy.template('understanding');m['limits']['max_repos']=True
        with self.assertRaises(Blocked):policy.validate(m)
    def test_unbounded_limit(self):
        m=policy.template('understanding');m['limits']['max_requests']=1000000
        with self.assertRaises(Blocked):policy.validate(m)
    def test_duplicate_target(self):
        m=policy.template('understanding');m['profile']['targets'].append(m['profile']['targets'][0])
        with self.assertRaises(Blocked):policy.validate(m)
    def test_wildcard_target(self):
        m=policy.template('understanding');m['profile']['targets'][0]['api']='ast.*'
        with self.assertRaises(Blocked):policy.validate(m)
    def test_executable_profile(self):
        m=policy.template('understanding');m['profile']['command']='python'
        with self.assertRaises(Blocked):policy.validate(m)
    def test_unknown_license(self):
        m=policy.template('understanding');m['permitted_licenses']=['GPL-3.0']
        with self.assertRaises(Blocked):policy.validate(m)
    def test_private_query(self):
        for q in ['is:private','https://internal/','password:hello','github_pat_secret','python\nexecute']:
            with self.subTest(q=q),self.assertRaises(Blocked):policy.query(q)
    def test_unsupported_destination(self):
        for k in ('delete','execute','web','upload'):
            with self.subTest(k=k),self.assertRaises(Blocked):policy.endpoint(k,{'repo':'x/y'})
    def test_bad_repo(self):
        for r in ('x/../../etc','https://github.com/x/y','x/y?token=z'):
            with self.subTest(r=r),self.assertRaises(Blocked):repo_name(r)
    def test_bad_paths(self):
        for p in ('../a.py','/a.py','a//b.py','a\\b.py','a/./b.py'):
            with self.subTest(p=p),self.assertRaises(Blocked):path_in_repo(p)
    def test_source_filters(self):
        for p in ('tests/a.py','.venv/a.py','vendor/a.py','x/test_f.py','x/thing.js'):
            self.assertFalse(policy.eligible(p,10,'100644',100))
        self.assertTrue(policy.eligible('src/parser.py',100,'100755',100))
        self.assertFalse(policy.eligible('src/a.py',100,'120000',100))
    def test_source_size_and_type(self):
        for n in (True,-1,0,101):self.assertFalse(policy.eligible('a.py',n,'100644',100))
    def test_policy_keeps_source_paths_not_commands(self):
        self.assertTrue(policy.url_for('file',{'repo':'a/b','path':'hello world.py','commit':'a'*40}).startswith('https://api.github.com/repos/a/b/contents/'))
    def test_no_numeric_json_overflow(self):
        for b in [b'{"x":1e999}',b'{"x":1,"x":2}',b'{"x":NaN}']:
            with self.assertRaises(Blocked):loads(b)
    def test_git_blob_not_plain_sha(self):self.assertEqual(git_sha(b''),'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391')

class QueueTests(Setup):
    def test_initial_status(self):self.assertEqual(self.store.snapshot(self.mid)['counts']['READY'],1)
    def test_single_global_claim(self):
        second=self.store.create_mission(self.spec);self.assertIsNotNone(self.store.claim(self.mid));self.assertIsNone(self.store.claim(second))
    def test_budget_charges_starts_only(self):
        self.step();self.assertEqual(self.store.snapshot(self.mid)['requests_used'],1);self.assertEqual(self.store.snapshot(self.mid)['tasks'][1]['attempts'],0)
    def test_wrong_token(self):
        p=self.store.claim(self.mid);p['token']='x'
        with self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
    def test_stale_reclaim(self):
        p=self.store.claim(self.mid);self.clock.advance(301);q=self.store.claim(self.mid)
        self.assertNotEqual(p['token'],q['token'])
        with self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
        self.disc.accept(q,self.transport.get(q))
    def test_expired_without_reclaim(self):
        p=self.store.claim(self.mid);self.clock.advance(301)
        with self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
    def test_duplicate_delivery(self):
        p,r=self.step();before=self.store.snapshot(self.mid)
        self.assertTrue(self.disc.accept(p,r)['duplicate']);self.assertEqual(before,self.store.snapshot(self.mid))
    def test_changed_replay(self):
        p,r=self.step()
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,b'{}',{},'synthetic_fixture'))
    def test_retry_exhaustion(self):
        p=self.store.claim(self.mid);self.store.fail(p,'NETWORK_UNAVAILABLE',retry=True)
        p=self.store.claim(self.mid);self.store.fail(p,'NETWORK_UNAVAILABLE',retry=True)
        self.assertIsNone(self.store.claim(self.mid));self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_WITH_GAPS')
    def test_cancel_revokes_claim(self):
        p=self.store.claim(self.mid);self.store.cancel(self.mid)
        with self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
        self.assertEqual(self.store.snapshot(self.mid)['status'],'CANCELLED')
    def test_restart_persists(self):
        self.step();again=Store(self.store.home,self.clock);self.assertEqual(again.snapshot(self.mid),self.store.snapshot(self.mid))
    def test_concurrent_claim(self):
        values=[]
        def f():values.append(Store(self.store.home,self.clock).claim(self.mid))
        ts=[threading.Thread(target=f) for _ in range(5)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(sum(v is not None for v in values),1)
    def test_request_budget(self):
        m=copy.deepcopy(self.spec);m['limits']['max_requests']=1;mid=self.store.create_mission(m);p=self.store.claim(mid);self.disc.accept(p,self.transport.get(p))
        self.assertEqual(self.store.snapshot(mid)['status'],'BUDGET_EXHAUSTED');self.assertIsNone(self.store.claim(mid))
    def test_mission_tamper(self):
        with self.store.tx() as c:c.execute('UPDATE mission SET spec=? WHERE id=?',(b'{}',self.mid))
        with self.assertRaises(Blocked):self.store.snapshot(self.mid)
    def test_hardlink_refused(self):
        os.link(self.store.path,self.root/'alias')
        with self.assertRaises(Blocked):Store(self.store.home)
    def test_symlink_refused(self):
        (self.root/'link').symlink_to(self.store.home,target_is_directory=True)
        with self.assertRaises(Blocked):Store(self.root/'link')
    def test_no_token_in_snapshot(self):
        p=self.store.claim(self.mid);self.assertNotIn(p['token'],canonical(self.store.snapshot()).decode())
    def test_delay_bounds(self):
        p=self.store.claim(self.mid)
        for x in [True,-1,float('nan'),86401]:
            with self.subTest(x=x),self.assertRaises(Blocked):self.store.fail(p,'ERROR',True,x)

class PipelineTests(Setup):
    def test_complete_pipeline(self):
        r=self.disc.run(self.mid,self.transport,steps=10);self.assertEqual(r['status'],'COMPLETED_FOR_BOUNDED_MISSION');self.assertEqual(r['counts']['DONE'],5);self.assertEqual(self.analyzer.calls,1)
    def test_no_match_is_explicit(self):
        with patch.object(self.analyzer,'scan',side_effect=lambda d,s,p:{'source_id':s,'source_sha256':sha(d),'registry_sha256':sha(canonical(p)),'findings':[],'functions_inspected':1}):
            r=self.disc.run(self.mid,self.transport,10);self.assertEqual(r['status'],'COMPLETED_NO_MATCH')
    def test_empty_search_not_useful_result(self):
        self.step({'items':[],'total_count':0,'incomplete_results':False});self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_NO_MATCH')
    def test_search_truncation(self):
        self.step({'items':[],'total_count':20,'incomplete_results':True});self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_WITH_GAPS')
    def test_duplicate_repo_one_task(self):
        self.step({'items':[{'full_name':'x/y'},{'full_name':'x/y'}],'total_count':2,'incomplete_results':False});self.assertEqual(self.store.task_count(self.mid,'repo'),1)
    def test_private_repo_rejected(self):
        self.step();p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['private']=True
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
        self.assertEqual(self.store.task_count(self.mid,'commit'),0)
    def test_unknown_license_metadata_only(self):
        self.step();p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['license']=None
        self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'));self.assertEqual(self.store.task_count(self.mid,'commit'),0)
    def test_repository_wrong_identity(self):
        self.step();p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['full_name']='other/repo'
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
    def test_commit_sha_required(self):
        self.step();self.step();p=self.store.claim(self.mid)
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical({'sha':'main','commit':{'tree':{'sha':TREE}}}),{},'synthetic_fixture'))
    def test_truncated_tree_is_gap(self):
        for _ in range(3):self.step()
        p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['truncated']=True;self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'));self.step()
        self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_WITH_GAPS')
    def test_tree_path_traversal(self):
        for _ in range(3):self.step()
        p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['tree'][0]['path']='../a.py'
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
    def test_file_bytes_verified(self):
        p=self.to_file();r=loads(self.transport.get(p).body);r['content']=base64.b64encode(SAMPLE.replace(b'ast.walk',b'ast.dump')).decode()
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
        self.assertEqual(self.analyzer.calls,0)
    def test_wrong_file_location(self):
        p=self.to_file();r=loads(self.transport.get(p).body);r['path']='another.py'
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
    def test_response_overflow(self):
        p=self.store.claim(self.mid)
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,b'x'*1_000_001,{},'synthetic_fixture'))
    def test_no_source_retained(self):
        self.disc.run(self.mid,self.transport,10)
        self.assertNotIn(SAMPLE.decode(),canonical(self.store.snapshot()).decode());self.assertNotIn(base64.b64encode(SAMPLE),self.store.path.read_bytes())
    def test_finding_binding(self):
        p=self.to_file()
        with patch.object(self.analyzer,'scan',return_value={'source_id':'fake'}),self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
    def test_untrusted_html_escaped(self):
        self.disc.run(self.mid,self.transport,10);r=self.store.snapshot(self.mid);r['title']='<script>bad()</script>';h=render(r)
        self.assertNotIn('<script>bad()',h);self.assertIn('&lt;script&gt;',h)
    def test_no_release_promotion(self):
        r=self.disc.run(self.mid,self.transport,10)
        self.assertFalse(r['release_approved']);self.assertFalse(r['candidates'][0]['reuse_approved'])
    def test_total_bytes_budget(self):
        m=copy.deepcopy(self.spec);m['limits']['max_total_source_bytes']=1;mid=self.store.create_mission(m);r=self.disc.run(mid,self.transport,10)
        self.assertEqual(r['status'],'COMPLETED_WITH_GAPS');self.assertEqual(self.analyzer.calls,0)
    def test_midstream_restart(self):
        self.step();self.step();other=Discovery(Store(self.store.home,self.clock),self.analyzer)
        r=other.run(self.mid,self.transport,10);self.assertEqual(r['counts']['DONE'],5)
    def test_unreviewed_provenance(self):
        p=self.store.claim(self.mid)
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,b'{}',{},'independently_verified'))

class NetworkTests(Setup):
    def test_429_stops_every_mission(self):
        p=self.store.claim(self.mid);self.disc.accept(p,Response(429,b'',{'retry-after':'120'},'synthetic_fixture'))
        other=self.store.create_mission(self.spec);self.assertIsNone(self.store.claim(other));self.clock.advance(121);self.assertIsNotNone(self.store.claim(other))
    def test_secondary_limit_minute(self):self.assertEqual(delay_from({},1000),60)
    def test_reset_header_honored(self):self.assertEqual(delay_from({'x-ratelimit-reset':'2000'},1000),1000)
    def test_malformed_header_bounded(self):
        for v in ('nan','inf','oops','-1'):self.assertEqual(delay_from({'retry-after':v},1000),60)
    def test_redirect_not_followed(self):
        from forge_core.github import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://elsewhere/'))
    def test_url_packet_not_authoritative(self):
        p=self.store.claim(self.mid);p['url']='http://127.0.0.1/secrets';client=GitHub()
        class Fake:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return b'{}'
            status=200;headers={}
        with patch.object(client.opener,'open',return_value=Fake()) as op:
            client.get(p);req=op.call_args[0][0];self.assertTrue(req.full_url.startswith('https://api.github.com/'));self.assertEqual(req.method,'GET')
    def test_user_agent_is_release_version(self):
        from forge_core import __version__
        p=self.store.claim(self.mid);client=GitHub()
        class Fake:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return b'{}'
            status=200;headers={}
        with patch.object(client.opener,'open',return_value=Fake()) as op:
            client.get(p);self.assertEqual(op.call_args[0][0].get_header('User-agent'),'FORGE-Workbench/'+__version__)
    def test_network_failure_recorded(self):
        class Dead:
            def get(self,p):raise Blocked('NETWORK_UNAVAILABLE')
        r=self.disc.run(self.mid,Dead(),10);self.assertEqual(r['status'],'RATE_WAIT');self.assertEqual(r['requests_used'],1)
    def test_404_no_retry(self):
        p=self.store.claim(self.mid);self.disc.accept(p,Response(404,b'',{},'synthetic_fixture'));self.assertIsNone(self.store.claim(self.mid))
    def test_5xx_bounded_retry(self):
        p=self.store.claim(self.mid);self.disc.accept(p,Response(503,b'',{},'synthetic_fixture'));self.clock.advance(61)
        p=self.store.claim(self.mid);self.disc.accept(p,Response(503,b'',{},'synthetic_fixture'));self.clock.advance(61);self.assertIsNone(self.store.claim(self.mid))

class ConsoleTests(Setup):
    def setUp(self):
        super().setUp();from forge_core.server import Console
        class App:
            def __init__(a,store):a.store=store;a.home=store.home
            def guard(a):pass
        self.server=Console(App(self.store));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.port=self.server.server_address[1]
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,path,method='GET',obj=None,token=True,origin=None,host=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=2);headers={}
        if token:headers['Authorization']='Bearer '+self.server.token
        if origin:headers['Origin']=origin
        if host:headers['Host']=host
        if obj is not None:headers['Content-Type']='application/json'
        c.request(method,path,canonical(obj) if obj is not None else None,headers);r=c.getresponse();body=r.read();st=r.status;c.close();return st,body
    def test_page_no_token_leak(self):
        st,b=self.request('/',token=False);self.assertEqual(st,200);self.assertNotIn(self.server.token.encode(),b)
    def test_pages_show_release_version(self):
        from forge_core import __version__
        for path in ('/','/opportunities','/contracts'):
            st,b=self.request(path,token=False);self.assertEqual(st,200)
            self.assertIn(__version__.encode(),b);self.assertNotIn(b'{{FORGE_VERSION}}',b)
    def test_missing_auth(self):self.assertEqual(self.request('/api/state',token=False)[0],403)
    def test_wrong_origin(self):self.assertEqual(self.request('/api/state',origin='https://evil.example')[0],403)
    def test_wrong_host(self):self.assertEqual(self.request('/api/state',host='evil.example')[0],403)
    def test_api_state(self):self.assertEqual(self.request('/api/state')[0],200)
    def test_create_mission_http(self):
        st,b=self.request('/api/missions','POST',{'template':'evaluation'});self.assertEqual(st,200);self.assertIn('id',loads(b))
    def test_source_code_not_accepted_as_command(self):self.assertEqual(self.request('/api/missions','POST',{'template':'evaluation','command':'run anything'})[0],400)
    def test_no_file_server(self):self.assertEqual(self.request('/../../etc/passwd')[0],404)
    def test_post_unauthenticated(self):self.assertEqual(self.request('/api/demo','POST',{},token=False)[0],400)
    def test_cancel_from_api(self):
        self.assertEqual(self.request('/api/cancel','POST',{'mission':self.mid})[0],200);self.assertEqual(self.store.snapshot(self.mid)['status'],'CANCELLED')

class ReviewTests(Setup):
    def test_cancel_releases_provider_slot(self):
        self.store.claim(self.mid);self.store.cancel(self.mid);other=self.store.create_mission(self.spec)
        self.assertIsNotNone(self.store.claim(other))
    def test_task_payload_tamper_is_blocked(self):
        with self.store.tx() as c:c.execute('UPDATE task SET payload=? WHERE mission=?',(canonical({'query':'changed','page':1}),self.mid))
        with self.assertRaises(Blocked):self.store.claim(self.mid)
    def test_no_duplicate_repo_across_queries(self):
        m=copy.deepcopy(self.spec);m['queries']=['python syntax','python scopes'];mid=self.store.create_mission(m)
        p=self.store.claim(mid);self.disc.accept(p,Response(200,canonical({'items':[{'full_name':'a/b'}],'total_count':1,'incomplete_results':False}),{},'synthetic_fixture'))
        p=self.store.claim(mid);self.disc.accept(p,Response(200,canonical({'items':[{'full_name':'a/b'},{'full_name':'c/d'}],'total_count':2,'incomplete_results':False}),{},'synthetic_fixture'))
        self.assertEqual(self.store.task_count(mid,'repo'),2)
    def test_json_must_be_object(self):
        p=self.store.claim(self.mid)
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,b'[]',{},'synthetic_fixture'))
    def test_sha_metadata_without_actual_bytes_fails(self):
        p=self.to_file();obj=loads(self.transport.get(p).body);obj['content']=''
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(obj),{},'synthetic_fixture'))
    def test_file_license_conflict(self):
        data=b'# SPDX-License-Identifier: GPL-3.0-only\nimport ast\n'
        for _ in range(3):self.step()
        p=self.store.claim(self.mid);obj=loads(self.transport.get(p).body);obj['tree'][0].update(size=len(data),sha=git_sha(data));self.disc.accept(p,Response(200,canonical(obj),{},'synthetic_fixture'))
        p=self.store.claim(self.mid);obj={'type':'file','path':'src/parse.py','size':len(data),'sha':git_sha(data),'encoding':'base64','content':base64.b64encode(data).decode()}
        self.disc.accept(p,Response(200,canonical(obj),{},'synthetic_fixture'))
        self.assertEqual(self.analyzer.calls,0);self.assertEqual(self.store.snapshot(self.mid)['status'],'COMPLETED_WITH_GAPS')
    def test_untrusted_links_not_followed(self):
        p=self.store.claim(self.mid);r={'items':[{'full_name':'a/b','url':'http://127.0.0.1/secret'}],'total_count':1,'incomplete_results':False}
        self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'));q=self.store.claim(self.mid)
        self.assertEqual(q['url'],'https://api.github.com/repos/a/b')
    def test_parser_failure_not_success(self):
        p=self.to_file()
        with patch.object(self.analyzer,'scan',side_effect=Blocked('TOOL_TIMEOUT')):
            with self.assertRaises(Blocked):self.disc.accept(p,self.transport.get(p))
        self.assertEqual(self.store.snapshot(self.mid)['counts']['DONE'],4)
    def test_success_at_zero_quota_defers_next(self):
        p=self.store.claim(self.mid);r=self.transport.get(p);r.headers={'x-ratelimit-remaining':'0','x-ratelimit-reset':str(self.clock()+100)}
        self.disc.accept(p,r);self.assertIsNone(self.store.claim(self.mid));self.clock.advance(101);self.assertIsNotNone(self.store.claim(self.mid))
    def test_plain_no_license_is_not_approved(self):
        r=self.disc.run(self.mid,self.transport,10);self.assertEqual(r['candidates'][0]['rights_status'],'DECLARATIONS_ONLY_REVIEW_REQUIRED')
    def test_result_corruption_detected(self):
        self.disc.run(self.mid,self.transport,10)
        with self.store.tx() as c:c.execute('UPDATE candidate SET result=?',(b'{}',))
        with self.assertRaises(Blocked):self.store.snapshot(self.mid)
    def test_wrong_tree_sha(self):
        for _ in range(3):self.step()
        p=self.store.claim(self.mid);r=loads(self.transport.get(p).body);r['sha']='a'*40
        with self.assertRaises(Blocked):self.disc.accept(p,Response(200,canonical(r),{},'synthetic_fixture'))
    def test_expired_twice_is_blocked_not_succeeded(self):
        self.store.claim(self.mid);self.clock.advance(301);self.store.claim(self.mid);self.clock.advance(301)
        self.assertIsNone(self.store.claim(self.mid));self.assertEqual(self.store.snapshot(self.mid)['counts']['BLOCKED'],1)
