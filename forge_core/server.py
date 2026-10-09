"""Loopback-only operator console. No arbitrary file serving or command execution."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,secrets,threading,os,time,subprocess,signal
from urllib.parse import urlsplit,parse_qs
from .common import *
from .workspace import Workbench,ROOT
from . import policy,demo
from .github import GitHub

class Console(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,workbench,port=0):
        super().__init__(('127.0.0.1',port),Handler);self.app=workbench
        self.token=secrets.token_urlsafe(32);self.operation={'status':'IDLE'};self.op_lock=threading.Lock();self.worker=None;self.child=None
        self.origin=f'http://127.0.0.1:{self.server_address[1]}'
    def casebook(self):
        self.app.guard()
        from .reuse import Casebook
        return Casebook(self.app.store)
    def start_operation(self,kind,mid=None):
        with self.op_lock:
            if self.worker and self.worker.is_alive():raise Blocked('OPERATION_RUNNING')
            self.operation={'status':'RUNNING','kind':kind,'started':time.time()}
            def job():
                try:
                    if kind=='demo':value=demo.run(self.app);summary={'mission':value['id'],'result':value['status']}
                    elif kind=='run':value=self.app.discovery().run(mid,GitHub(os.environ.get('FORGE_GITHUB_TOKEN')),steps=30,seconds=120);summary={'mission':mid,'result':value['status']}
                    elif kind=='opportunity':
                        from .opportunity_demo import run as run_opportunity
                        value=run_opportunity(self.app);summary={'result':value['status'],'run':value['run'],'summary':value['summary']}
                    elif kind=='contract':
                        from .contract_trial import run as run_contract
                        dest=self.app.home/'reports'/('contract-'+secrets.token_hex(6))
                        value=run_contract(self.app,dest);summary={'result':value['status'],'path':str(dest),'checks':value['external_vector_report']['summary'],'reference':value['reference_report']['status']}
                    elif kind=='mixed':
                        from .corpus_demo import run as run_corpus_demo
                        value=run_corpus_demo(self.app);summary={'result':value['status'],'corpus':value['run_id']}
                    elif kind=='reuse':
                        from .reuse_trial import run as reuse_run
                        value=reuse_run(self.app);summary={'result':value['status'],'case_id':value['case_id'],'mission':value['mission'],'verdicts':value['verdicts']}
                    elif kind=='cycle':
                        out=self.app.home/'evaluations'/('run-'+secrets.token_hex(6));out.parent.mkdir(exist_ok=True)
                        cmd=self.app.cycle_command(out);log=out.parent/(out.name+'.log')
                        with log.open('xb') as stream:
                            self.child=subprocess.Popen(cmd,cwd=self.app.home,env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','HOME','TMPDIR') }|{'PYTHONDONTWRITEBYTECODE':'1'},stdout=stream,stderr=subprocess.STDOUT,start_new_session=os.name=='posix')
                            try:rc=self.child.wait(timeout=300)
                            except subprocess.TimeoutExpired:
                                self.stop_child();raise Blocked('EVALUATION_TIMEOUT')
                        rec=loads(read(out/'demo.json',8_000_000))
                        if rc or rec.get('status')!='SUPERVISED_PROFILE_CYCLE_REPRODUCED':raise Blocked('EVALUATION_FAILED')
                        summary={'result':rec['status'],'path':str(out),'model_calls':0}
                    else:raise Blocked('OPERATION_KIND')
                    self.operation={'status':'FINISHED','kind':kind,**summary}
                except Exception as e:self.operation={'status':'BLOCKED','kind':kind,'reason':str(e) if isinstance(e,Blocked) else type(e).__name__}
                write(self.app.home/'last_operation.json',canonical(self.operation))
            self.worker=threading.Thread(target=job,name='forge-bounded-operation',daemon=True);self.worker.start()
    def stop_child(self):
        if self.child and self.child.poll() is None:
            if os.name=='posix':os.killpg(self.child.pid,signal.SIGTERM)
            else:self.child.terminate()
            try:self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:self.child.kill();self.child.wait()
    def server_close(self):self.stop_child();super().server_close()

class Handler(BaseHTTPRequestHandler):
    server_version='FORGEWorkbench'
    def log_message(self,*a):pass
    def send(self,status,body,mime='application/json'):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers();self.wfile.write(body)
    def check_host(self):
        if self.headers.get('Host')!=self.server.origin.removeprefix('http://'):raise Blocked('HOST_DENIED')
    def authorize(self):
        self.check_host()
        if self.headers.get('Origin',self.server.origin)!=self.server.origin:raise Blocked('ORIGIN_DENIED')
        auth=self.headers.get('Authorization','')
        if not secrets.compare_digest(auth,'Bearer '+self.server.token):raise Blocked('AUTH_REQUIRED')
    def do_GET(self):
        try:
            self.check_host();path=urlsplit(self.path).path
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/opportunities':'opportunities.html','/opportunities.js':'opportunities.js','/contracts':'contracts.html','/contracts.js':'contracts.js'}
            if path in static:
                f=ROOT/'assets'/static[path];mime={'html':'text/html; charset=utf-8','js':'text/javascript','css':'text/css'}[f.suffix[1:]];return self.send(200,read(f),mime)
            self.authorize()
            if path=='/api/state':value={'missions':self.server.app.store.snapshot(),'operation':self.server.operation,'reuse_cases':self.server.casebook().list(),'templates':[{'id':n,'title':policy.template(n)['title']} for n in ['understanding','reliability','evaluation']]}
            elif path=='/api/integration-contract':
                self.server.app.guard()
                from .interop import contract
                value=contract()
            elif path=='/api/opportunities':
                from .opportunities import OpportunityStore
                value={'runs':OpportunityStore(self.server.app).list()}
            elif path=='/api/opportunity':
                from .opportunities import OpportunityStore
                query=parse_qs(urlsplit(self.path).query)
                if set(query)!={'run'} or len(query['run'])!=1:raise Blocked('OPPORTUNITY_QUERY')
                value=OpportunityStore(self.server.app).get(query['run'][0])
            elif path=='/api/corpora':
                from .corpus import list_runs
                value={'corpora':list_runs(self.server.app)}
            elif path=='/api/corpus':
                from .corpus import get,search
                query=parse_qs(urlsplit(self.path).query)
                if not {'run','q'}<=set(query) or set(query)-{'run','q','distinct','include_tests','required_api','exclude_api','kind'} or any(len(v)!=1 for v in query.values()):raise Blocked('CORPUS_QUERY')
                for flag in ('distinct','include_tests'):
                    if flag in query and query[flag][0] not in ('0','1'):raise Blocked('CORPUS_QUERY_BOOL')
                filters={}
                for arg,key in [('required_api','required_apis'),('exclude_api','exclude_apis')]:
                    if arg in query:filters[key]=query[arg][0].split(',')
                if 'kind' in query:filters['record_kind']=query['kind'][0]
                value=search(get(self.server.app,query['run'][0]),query['q'][0],distinct=query.get('distinct',['0'])[0]=='1',include_tests=query.get('include_tests',['0'])[0]=='1',filters=filters)
            elif path=='/api/corpus-context':
                from .corpus import get,dependency_context
                query=parse_qs(urlsplit(self.path).query)
                if set(query)!={'run','definition'} or any(len(v)!=1 for v in query.values()):raise Blocked('CORPUS_CONTEXT_QUERY')
                value=dependency_context(get(self.server.app,query['run'][0]),query['definition'][0])
            elif path=='/api/reuse':
                query=parse_qs(urlsplit(self.path).query)
                if set(query)!={'case'} or len(query['case'])!=1:raise Blocked('CASE_QUERY')
                value=self.server.casebook().compare(query['case'][0])
            else:return self.send(404,canonical({'error':'NOT_FOUND'}))
            self.send(200,canonical(value))
        except Blocked as e:self.send(403,canonical({'error':str(e)}))
        except Exception:self.send(500,canonical({'error':'SERVER_ERROR'}))
    def do_POST(self):
        try:
            self.authorize()
            if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):raise Blocked('CONTENT_TYPE')
            try:n=int(self.headers.get('Content-Length','-1'))
            except ValueError:raise Blocked('BODY_LENGTH')
            path=urlsplit(self.path).path
            integer(n,2,400000 if path=='/api/opportunity/analyze' else 16000);obj=loads(self.rfile.read(n))
            if not isinstance(obj,dict):raise Blocked('OBJECT_REQUIRED')
            if path=='/api/missions':
                if set(obj)-{'template','public_brief','query'}:raise Blocked('MISSION_FIELDS')
                m=policy.template(obj.get('template'))
                if obj.get('public_brief'):m['public_brief']=text(obj['public_brief'],500)
                if obj.get('query'):m['queries']=[obj['query']]
                self.server.app.guard();value={'id':self.server.app.store.create_mission(m)}
            elif path=='/api/contract-trial':
                if obj:raise Blocked('FIXED_TRIAL_NO_ARGUMENTS')
                self.server.start_operation('contract');value={'started':True}
            elif path in {'/api/interop/fingerprint','/api/interop/verify'}:
                self.server.app.guard()
                from .interop import fingerprint,verify_record
                required={'raw_json'} if path.endswith('fingerprint') else {'raw_json','receipt'}
                if set(obj)!=required or not isinstance(obj['raw_json'],str):raise Blocked('INTEROP_FIELDS')
                try:raw=obj['raw_json'].encode('utf-8')
                except UnicodeError:raise Blocked('INVALID_UNICODE')
                value=fingerprint(raw) if path.endswith('fingerprint') else verify_record(raw,obj['receipt'])
            elif path=='/api/opportunity/demo':
                if obj:raise Blocked('NO_DEMO_ARGUMENTS')
                self.server.start_operation('opportunity');value={'started':True}
            elif path=='/api/opportunity/analyze':
                from .opportunities import OpportunityStore
                from .corpus import get
                if not {'bundle'}<=set(obj) or set(obj)-{'bundle','corpora'}:raise Blocked('OPPORTUNITY_REQUEST')
                names=obj.get('corpora',[])
                if not isinstance(names,list) or len(names)>4 or any(not isinstance(n,str) for n in names):raise Blocked('CORPUS_SELECTION')
                value=OpportunityStore(self.server.app).run(obj['bundle'],[(name,get(self.server.app,name)) for name in names])
            elif path=='/api/opportunity/guided':
                from .opportunities import OpportunityStore,guided_bundle
                value=OpportunityStore(self.server.app).run(guided_bundle(obj))
            elif path=='/api/opportunity/draft':
                from .opportunities import draft
                if set(obj)!={'source'}:raise Blocked('OPPORTUNITY_REQUEST')
                value=draft(obj['source'])
            elif path=='/api/opportunity/feedback':
                from .opportunities import OpportunityStore
                if set(obj)!={'run','note'}:raise Blocked('OPPORTUNITY_REQUEST')
                value=OpportunityStore(self.server.app).feedback(obj['run'],obj['note'])
            elif path=='/api/opportunity/export':
                from .opportunities import OpportunityStore
                if set(obj)!={'run'}:raise Blocked('OPPORTUNITY_REQUEST')
                dest=self.server.app.home/'reports'/('opportunity-'+secrets.token_hex(6))
                value=OpportunityStore(self.server.app).export(obj['run'],dest);value['path']=str(dest)
            elif path=='/api/run':self.server.start_operation('run',obj['mission']);value={'started':True}
            elif path=='/api/demo':self.server.start_operation('demo');value={'started':True}
            elif path=='/api/cycle':self.server.start_operation('cycle');value={'started':True}
            elif path=='/api/corpus-demo':
                if obj:raise Blocked('NO_DEMO_ARGUMENTS')
                self.server.start_operation('mixed');value={'started':True}
            elif path=='/api/reuse-demo':
                if obj:raise Blocked('NO_DEMO_ARGUMENTS')
                self.server.start_operation('reuse');value={'started':True}
            elif path=='/api/reuse':
                if set(obj)!={'mission','contract'}:raise Blocked('REUSE_REQUEST_FIELDS')
                value=self.server.casebook().create(obj['mission'],obj['contract'])
            elif path=='/api/reuse/evidence':
                if set(obj)!={'case','note'}:raise Blocked('REUSE_REQUEST_FIELDS')
                value=self.server.casebook().record(obj['case'],obj['note'])
            elif path=='/api/reuse/export':
                if set(obj)!={'case'}:raise Blocked('REUSE_REQUEST_FIELDS')
                dest=self.server.app.home/'reports'/('reuse-'+secrets.token_hex(6))
                value=self.server.casebook().export(obj['case'],dest);value['path']=str(dest)
            elif path=='/api/cancel':self.server.app.store.cancel(obj['mission']);value={'cancelled':True}
            elif path=='/api/export':
                dest=self.server.app.home/'reports'/('report-'+secrets.token_hex(6));value=self.server.app.export(obj['mission'],dest);value['path']=str(dest)
            elif path=='/api/shutdown':
                if self.server.worker and self.server.worker.is_alive():raise Blocked('FINISH_OR_CANCEL_ACTIVE_OPERATION_FIRST')
                value={'stopped':True};threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:return self.send(404,canonical({'error':'NOT_FOUND'}))
            self.send(200,canonical(value))
        except (Blocked,KeyError,ValueError) as e:self.send(400,canonical({'error':str(e) if isinstance(e,Blocked) else 'INVALID_REQUEST'}))
        except Exception:self.send(500,canonical({'error':'SERVER_ERROR'}))
