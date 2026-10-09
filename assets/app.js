'use strict';
let token=location.hash.slice(1)||sessionStorage.getItem('forge-token')||'';history.replaceState(null,'',location.pathname);if(token)sessionStorage.setItem('forge-token',token);
let current=null,selected=null,busy=false,stopped=false;
const $=id=>document.getElementById(id);const el=(t,text,cls)=>{const n=document.createElement(t);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
function stateLabel(s){return ({OBSERVED:'API observed',UNKNOWN:'Evidence missing',REVIEWED_SUPPORT:'Operator support',TEST_PASSED:'Test passed',TEST_FAILED:'Test failed',CONTRADICTED:'Contradicted',READY_FOR_INTEGRATION_REVIEW:'Ready for integration review',NEEDS_EVIDENCE:'Needs evidence',STALE_SOURCE_REVIEW_REQUIRED:'Source changed — recheck',CURRENT_REVIEW:'Current review'})[s]||s;}
function fieldLabel(t,id){const l=el('label',t);l.htmlFor=id;return l;}
function notice(s){$('notice').textContent=s;}
async function api(path,obj){const r=await fetch(path,{method:obj===undefined?'GET':'POST',headers:{'Authorization':'Bearer '+token,...(obj===undefined?{}:{'Content-Type':'application/json'})},body:obj===undefined?undefined:JSON.stringify(obj)});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
function button(text,fn,cls){const b=el('button',text,cls);b.onclick=()=>fn().catch(e=>notice(e.message));return b;}
function showDetail(m){selected=m.id;const d=$('detail');d.hidden=false;d.replaceChildren(el('h2',m.title),el('p',m.status,'state'),el('p',m.scope,'muted'));for(const c of m.candidates){const a=el('article',undefined,'finding');a.append(el('h3',c.repository+' / '+c.path),el('p','Commit '+c.commit,'code'),el('p',c.rights_status,'muted'));for(const f of c.findings)a.append(el('b',f.resolved_api),el('p',(f.qualified_name||'<module>')+' · '+f.capability));if(!c.findings.length)a.append(el('p','No configured API match. This does not establish absence of useful code.'));d.append(a);}for(const g of m.gaps)d.append(el('p',g,'muted'));for(const t of m.tasks.filter(t=>t.error))d.append(el('p',t.kind+': '+t.error,'code'));if(!m.candidates.length)d.append(el('p','No source observations yet. Check the task state and provider access.'));}
function render(d){current=d;busy=d.operation.status==='RUNNING';$('operation').textContent=JSON.stringify(d.operation,null,2);$('connection').textContent='Local · connected';$('demo').disabled=busy;$('cycle').disabled=busy;$('reuse-demo').disabled=busy;const board=$('missions');board.replaceChildren();if(!d.missions.length)board.append(el('p','No missions yet. Create one, or run the safe demonstration.','muted'));for(const m of d.missions){const row=el('article',undefined,'mission'),text=el('div'),actions=el('div');text.append(el('h3',m.title),el('p',m.status,'state'),el('p',`${m.requests_used}/${m.request_budget} request attempts · ${m.counts.DONE} steps complete · ${m.candidates.length} source observations`,'muted'));const run=button('Run / resume',async()=>{await api('/api/run',{mission:m.id});notice('Bounded GitHub run started. Results will appear here.');await poll();},'secondary');run.disabled=busy||m.status==='CANCELLED';actions.append(button('Compare for reuse',async()=>createReuse(m),'secondary'),run,button('Inspect',async()=>showDetail(m),'quiet'),button('Export',async()=>{const r=await api('/api/export',{mission:m.id});notice('Report saved locally: '+r.path);},'quiet'),button('Cancel',async()=>{await api('/api/cancel',{mission:m.id});await poll();},'quiet'));row.append(text,actions);board.append(row);}if(selected){const m=d.missions.find(x=>x.id===selected);if(m)showDetail(m);}renderReuseList(d.reuse_cases||[]);}
async function poll(){if(stopped)return;try{render(await api('/api/state'));}catch(e){$('connection').textContent='Disconnected';notice(e.message);}}
$('mission-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/missions',{template:$('template').value,public_brief:$('brief').value,query:$('query').value});notice('Mission created. Run it when ready to make public GitHub requests.');await poll();}catch(e){notice(e.message);}};
$('demo').onclick=async()=>{try{await api('/api/demo',{});notice('Running synthetic discovery with the real Hound scanner.');await poll();}catch(e){notice(e.message);}};
$('cycle').onclick=async()=>{try{await api('/api/cycle',{});notice('Running the fixed supplied profile proposals and all inherited tests. This is a supervised replay.');await poll();}catch(e){notice(e.message);}};
$('shutdown').onclick=async()=>{try{await api('/api/shutdown',{});stopped=true;notice('Console stopped. Saved missions remain in your local workspace.');$('connection').textContent='Stopped';}catch(e){notice(e.message);}};

async function createReuse(m){
 const panel=$('reuse-detail');panel.hidden=false;panel.replaceChildren(el('h2','Define the reuse requirement'),el('p','For '+m.title));
 const form=el('form');
 const label=el('label','Title'),title=el('input');title.value='Reuse review: '+m.title;title.maxLength=160;title.id='reuse-title';label.htmlFor=title.id;
 const objective=el('input');objective.placeholder='What must the component do?';objective.required=true;objective.maxLength=1200;objective.id='reuse-objective';
 const apis=el('input');apis.placeholder='Optional exact API names separated by commas';apis.id='reuse-apis';
 const behaviors=el('textarea');behaviors.placeholder='Behavior requirements, one per line. These remain unknown until an authorized test exists.';behaviors.rows=3;behaviors.id='reuse-behaviors';
 const review=el('textarea');review.value='Dependencies and environment fit\nIntended reuse rights and limitations';review.rows=3;review.id='reuse-review';
 const submit=el('button','Freeze requirements');submit.type='submit';
 form.append(label,title,fieldLabel('Objective','reuse-objective'),objective,fieldLabel('Syntactic API requirements (not behavior proof)','reuse-apis'),apis,fieldLabel('Behavior requirements','reuse-behaviors'),behaviors,fieldLabel('Operator review requirements','reuse-review'),review,submit);
 form.onsubmit=async e=>{e.preventDefault();try{
  const rs=[];for(const api of apis.value.split(',').map(x=>x.trim()).filter(Boolean))rs.push({id:'api_'+rs.length,label:'Declared call to '+api,kind:'observed_api',required:true,apis:[api]});
  for(const text of behaviors.value.split('\n').map(x=>x.trim()).filter(Boolean))rs.push({id:'behavior_'+rs.length,label:text,kind:'behavior_test',required:true});
  for(const text of review.value.split('\n').map(x=>x.trim()).filter(Boolean))rs.push({id:'review_'+rs.length,label:text,kind:'review',required:true});
  const c=await api('/api/reuse',{mission:m.id,contract:{schema:1,title:title.value,objective:objective.value,requirements:rs}});await showReuse(c.id);await poll();
 }catch(e){notice(e.message);}};panel.append(form);panel.scrollIntoView({behavior:'smooth'});
}
function renderReuseList(cases){const list=$('reuse-cases');list.replaceChildren();if(!cases.length)list.append(el('p','No decisions yet. Compare a mission, or run the first-party reuse trial.','muted'));for(const c of cases){const row=el('article',undefined,'mission');row.append(el('h3',c.title),button('Review evidence',()=>showReuse(c.id),'secondary'),button('Export packet',async()=>{const r=await api('/api/reuse/export',{case:c.id});notice('Reuse packet saved locally: '+r.path);},'quiet'));list.append(row);}}
async function showReuse(id){const d=await api('/api/reuse?case='+encodeURIComponent(id)),panel=$('reuse-detail');panel.hidden=false;panel.replaceChildren(el('h2',d.title),el('p',d.objective),el('p',stateLabel(d.status),'state'),el('p','Source observations and operator statements do not establish runtime correctness. No automatic adoption or deployment.','muted'));
 for(const row of d.rows){const article=el('article',undefined,'finding'),c=row.candidate;article.append(el('h3',c.qualified_name||c.path),el('p',stateLabel(row.verdict),'state'),el('p',c.source_id,'code'),el('p','SHA-256 '+c.source_sha256,'code'));
 const table=el('table'),head=el('tr');for(const h of ['Requirement','Evidence state','Basis'])head.append(el('th',h));table.append(head);
 for(const req of row.requirements){const tr=el('tr'),basis=el('td',req.basis);tr.append(el('td',req.label),el('td',stateLabel(req.state),'evidence-state'),basis);
 if(req.kind==='review')basis.append(button('Record review',async()=>{
   const verdict=prompt('Record SUPPORTED, CONTRADICTED or UNKNOWN. This is an operator statement, not a test result.','UNKNOWN');if(verdict===null)return;
   const note=prompt('Explain the source evidence, dependencies and limitations.');if(note===null)return;
   await api('/api/reuse/evidence',{case:id,note:{candidate:c.key,requirement:req.id,verdict,note,actor:'Local operator',supersedes:req.evidence_ids[0]||null}});await showReuse(id);
 },'quiet'));table.append(tr);}article.append(table);panel.append(article);
 }panel.scrollIntoView({behavior:'smooth'});
}
$('reuse-demo').onclick=async()=>{try{await api('/api/reuse-demo',{});notice('Running source inspection and fixed first-party behavior tests.');await poll();}catch(e){notice(e.message);}};
poll();setInterval(poll,2000);

async function refreshCorpora(){try{const d=await api('/api/corpora'),select=$('corpus-run'),previous=select.value;select.replaceChildren();for(const r of d.corpora){const o=el('option',r.id+' — '+r.status);o.value=r.id;select.append(o);}if([...select.options].some(o=>o.value===previous))select.value=previous;$('corpus-summary').textContent=d.corpora.length?'Choose a run. Results are static observations; old source claims are not executed test evidence.':'No mixed-code corpus yet. Run the fixed demonstration or import an approved Drive or pinned GitHub snapshot through the local CLI.';}catch(e){notice(e.message);}}
if($('corpus-form')){
 $('corpus-refresh').onclick=refreshCorpora;
 $('corpus-demo').onclick=async()=>{try{await api('/api/corpus-demo',{});notice('Inspecting the bundled mixed-file example with Hound. No collected function executes.');await poll();}catch(e){notice(e.message);}};
 $('corpus-form').onsubmit=async e=>{
   e.preventDefault();
   try {
     const run=$('corpus-run').value;
     const params=new URLSearchParams({run,q:$('corpus-query').value,distinct:$('corpus-distinct').checked?'1':'0',include_tests:$('corpus-tests').checked?'1':'0'});
     if($('corpus-api').value.trim()) params.set('required_api',$('corpus-api').value.trim());
     const d=await api('/api/corpus?'+params);
     $('corpus-summary').textContent=d.total_matching_occurrences+' matching occurrences in '+d.distinct_matching_groups+' text groups; '+d.full_match_count+' match every metadata query term. '+(d.search_status==='PARTIAL_TERM_MATCHES_ONLY'?'Only partial-term matches found. ':'')+d.rank_meaning+' '+d.warnings.join(' ');
     const root=$('corpus-results');root.replaceChildren();
     for(const r of d.results){
       const a=el('article');
       a.append(el('h3',r.name),el('p',(r.logical_path_hint||r.path)+' · lines '+r.start_line+'–'+r.end_line),el('p',r.container+' → '+r.member_chain.map(x=>x.path).join(' → '),'muted'),el('p',r.runtime_status+' · '+r.source_id,'code'));
       const why=el('details');why.append(el('summary','Why this matched — evidence, not confidence'),el('pre',JSON.stringify({matched_fields:r.matched_fields,ranking:r.rank_breakdown,observed_apis:r.observed_apis,declared_call_syntax:r.declared_calls,declared_import_leads:r.declared_api_leads,class_documentation:r.class_context,method_role:r.method_role,implementation_kind:r.implementation_kind,attribute_read_syntax:r.attribute_reads,comparison_operators:r.comparison_operators},null,2),'code'));a.append(why);
       if(r.occurrences){const origins=el('details');origins.append(el('summary',r.occurrences.length+' matching source location(s)'),el('p','Identical text does not prove identical dependencies or reuse rights.'),el('pre',JSON.stringify(r.occurrences,null,2),'code'));a.append(origins);}
       if(r.definition_id){
         const button=el('button','Inspect helper context','secondary');
         button.onclick=async()=>{try{const c=await api('/api/corpus-context?'+new URLSearchParams({run,definition:r.definition_id}));const detail=el('details');detail.open=true;detail.append(el('summary','Source-bound helper leads — runtime resolution not established'),el('pre',JSON.stringify(c,null,2),'code'));a.append(detail);button.disabled=true;}catch(error){notice(error.message);}};
         a.append(button);
       }
       root.append(a);
     }
   }catch(error){notice(error.message);}
 };
}
