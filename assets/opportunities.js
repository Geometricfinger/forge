'use strict';
let token=location.hash.slice(1)||sessionStorage.getItem('forge-token')||'';
history.replaceState(null,'',location.pathname);if(token)sessionStorage.setItem('forge-token',token);
const $=x=>document.getElementById(x),el=(tag,txt,cls)=>{const e=document.createElement(tag);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e;};
const say=t=>$('opp-notice').textContent=t;
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
function button(label,fn){const b=el('button',label,'secondary');b.onclick=()=>fn().catch(e=>say(e.message));return b;}
function label(s){return s.toLowerCase().replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());}
const beforeRun=$('opp-before'), afterRun=$('opp-after');
let comparisonRequest=0;

function resetComparison(){
  comparisonRequest++;
  $('opp-compare').disabled=!beforeRun.value || !afterRun.value;
  $('opp-compare-summary').textContent='';
  $('opp-compare-results').replaceChildren();
}

function updateRunChoices(runs){
  // A completed demo can refresh the board while a selection is open.
  if(beforeRun.options.length===runs.length+1 &&
     runs.every((run,i)=>beforeRun.options[i+1].value===run.id))return;
  let length=16; // Includes the opp_ prefix and twelve hex digits.
  while(new Set(runs.map(run=>run.id.slice(0,length))).size<runs.length)length+=4;
  for(const select of [beforeRun,afterRun]){
    const selected=select.value;
    const prompt=el('option','Choose a run');
    prompt.value='';
    select.replaceChildren(prompt);
    for(const run of runs){
      const option=el('option',run.title+' · '+run.id.slice(0,length));
      option.value=run.id;
      select.append(option);
    }
    select.value=runs.some(run=>run.id===selected)?selected:'';
  }
  if(!beforeRun.value || !afterRun.value)resetComparison();
}

function citationDetails(citations){
  const details=el('details');
  details.append(el('summary','Citations ('+citations.length+')'));
  details.ontoggle=()=>{
    if(!details.open)return;
    details.ontoggle=null;
    details.append(el('p','Character ranges start at zero and exclude the end position.','hint'));
    const list=el('ul');
    const range=endpoint=>endpoint?'['+endpoint.start+', '+endpoint.end+')':'—';
    for(const cite of citations){
      const item=el('li');
      item.append(el('strong',label(cite.status)+' · '+cite.source),
        el('p',label(cite.role)+' '+cite.owner+' · Before '+range(cite.before)+' · After '+range(cite.after)));
      if(cite.source_text_changed)item.append(el('p','Source text changed.','hint'));
      if(cite.source_metadata_changed)item.append(el('p','Source metadata changed.','hint'));
      list.append(item);
    }
    details.append(list);
  };
  return details;
}

function renderComparison(comparison){
  const counts=comparison.summary;
  $('opp-compare-summary').textContent=[
    counts.total+(counts.total===1?' hypothesis':' hypotheses'),
    counts.review_required+' for review', counts.retained+' with citations retained',
    counts.added+' added', counts.removed+' removed'
  ].join(' · ');
  const results=$('opp-compare-results');
  results.replaceChildren();
  if(comparison.scope)results.append(el('p',comparison.scope,'hint'));
  for(const hypothesis of comparison.hypotheses){
    const row=el('article',undefined,'finding');
    row.append(el('h3',hypothesis.title),el('p',label(hypothesis.status),'state'),el('small',hypothesis.id));
    if(hypothesis.reasons.length){
      const reasons=el('ul');
      for(const reason of hypothesis.reasons){
        reasons.append(el('li',/^[A-Z][A-Z0-9_]+$/.test(reason)?label(reason):reason));
      }
      row.append(reasons);
    }
    if(hypothesis.citations.some(c=>c.source_text_changed && (c.status==='RETAINED' || c.status==='MOVED'))){
      row.append(el('p','A source changed while its cited wording stayed the same. Review the passage in context.','hint'));
    }
    if(hypothesis.citations.length)row.append(citationDetails(hypothesis.citations));
    results.append(row);
  }
}

beforeRun.onchange=afterRun.onchange=resetComparison;
$('opp-compare-form').onsubmit=async event=>{
  event.preventDefault();
  if(!beforeRun.value || !afterRun.value)return;
  const current=++comparisonRequest;
  const query=new URLSearchParams({before:beforeRun.value,after:afterRun.value});
  $('opp-compare').disabled=true;
  $('opp-compare-summary').textContent='Comparing retained citations…';
  $('opp-compare-results').replaceChildren();
  try{
    const comparison=await api('/api/opportunity/compare?'+query);
    if(current===comparisonRequest)renderComparison(comparison);
  }catch(e){
    if(current===comparisonRequest)$('opp-compare-summary').textContent=e.message;
  }finally{
    if(current===comparisonRequest)$('opp-compare').disabled=false;
  }
};

async function refresh(){const d=await api('/api/opportunities'),area=$('opp-runs');updateRunChoices(d.runs);area.replaceChildren();if(!d.runs.length)area.append(el('p','No investigations yet. Run the case study or import a reviewed brief.'));for(const r of d.runs){const a=el('article',undefined,'mission');a.append(el('h3',r.title),el('p',r.summary.hypotheses+' hypotheses · '+r.summary.sources+' sources'),button('Inspect evidence',()=>show(r.id)),button('Export private packet',async()=>{const p=await api('/api/opportunity/export',{run:r.id});say('Saved locally: '+p.path);}));area.append(a);}return d;}
function appendReviewLeads(detail,group){
  // Old saved reports retained only a count, not inspectable partial records.
  if(!Object.hasOwn(group,'review_candidates'))return;
  detail.append(el('h5','Partial matches to inspect'));
  if(!group.review_candidates.length){
    detail.append(el('p','No partial review leads retained for this mechanism.','hint'));
    return;
  }
  detail.append(el('p','Review only. Inspect complete sources and refine the reviewed query before selecting a lead.','hint'));
  for(const row of group.review_candidates){
    detail.append(el('p',row.name+' · '+row.corpus+' · '+(row.definition_id||row.source_id),'code'),
      el('p','Query: '+row.query+' · Rank: '+row.rank),
      el('p','Matched terms: '+((row.matched_query_terms||[]).join(', ')||'Not recorded')),
      el('p','Missing terms: '+((row.missing_query_terms||[]).join(', ')||'Not recorded')));
  }
  if(group.review_candidates_omitted)detail.append(el('p',group.review_candidates_omitted+' additional distinct partial leads omitted from this shortlist.','hint'));
}
function appendBuildOptions(parent,hypothesis){
  const plan=hypothesis.build_options;
  if(!plan)return; // Historical reports did not retain synthesized routes.
  const section=el('section');
  section.append(el('h3','Build options for review'),el('p',label(plan.status),'state'),
    el('p','Required output: '+plan.target_field),
    el('p','These routes combine reviewed declarations. Different mechanism sets do not establish different capabilities, tested behavior or novelty.','hint'));
  if(plan.next_decision)section.append(el('p','Next decision: '+label(plan.next_decision)));
  if(plan.required_conditions.length)section.append(el('p','Required conditions: '+plan.required_conditions.join(', ')));
  const notices={
    EXISTING_OPTION_REVIEW_REQUIRED:'Review the cited existing option for this exact task before proposing new work.',
    EVIDENCE_REVIEW_REQUIRED:'Resolve the supporting, contrary or stale evidence before generating build routes.',
    NO_NEW_WORK_DECLARED_OUTPUT_AVAILABLE:'The required output is already available in the declared workflow. No new build follows from this map.',
    NO_DECLARED_ROUTE:'No complete route follows from these declarations. Review missing inputs and conditions; an absent route does not establish that no external solution exists.',
    NO_PLAN_FOUND_WITHIN_BOUNDS:'No complete route was retained within the search bounds. Review the limits and unresolved inputs before drawing a conclusion.'
  };
  if(notices[plan.status])section.append(el('p',notices[plan.status]));
  const list=values=>{const node=el('ul');for(const value of values)node.append(el('li',value));return node;};
  const evidence=refs=>el('p',refs.length?'Evidence: '+refs.map(ref=>ref.source+' ['+ref.start+', '+ref.end+')').join('; '):'No condition declaration retained.','hint');
  const conditions=rows=>{
    const node=el('ul');
    for(const row of rows){const item=el('li',row.condition+': '+label(row.status));item.append(evidence(row.evidence));node.append(item);}
    return node;
  };
  if(plan.options.length)section.append(el('p','Review order considers unconfirmed conditions, new work and route length. It is not a usefulness, cost or success ranking.','hint'));
  for(const [index,option] of plan.options.entries()){
    const detail=el('details');
    detail.append(el('summary','Option '+(index+1)+' · '+label(option.classification)+' · '+option.steps.length+' steps'+(option.unconfirmed_conditions.length?' · conditions unconfirmed':'')),
      el('p',option.id,'hint'),el('p','Operating conditions: '+label(option.condition_status)));
    if(option.unconfirmed_conditions.length)detail.append(el('h4','Conditions still unknown'),list(option.unconfirmed_conditions.map(row=>row.mechanism+': '+row.condition)));
    if(option.unimplemented_mechanisms.length)detail.append(el('h4','New work requiring implementation and evidence'),list(option.unimplemented_mechanisms));
    else detail.append(el('p','No step is declared as new work; the complete route still needs validation.','hint'));
    const steps=el('ol');
    for(const step of option.steps){
      const item=el('li');
      item.append(el('h4',step.title),el('p',label(step.role)+' · '+step.mechanism),el('p',step.description),
        el('p','Requires: '+(step.requires.join(', ')||'No declared input')),el('p','Provides: '+(step.provides.join(', ')||'No declared output')));
      if(step.input_bindings.length)item.append(el('h5','Input sources'),list(step.input_bindings.map(row=>row.field+' ← '+(row.origin==='workflow'?'workflow input':'step '+row.mechanism))));
      if(step.condition_checks.length)item.append(el('h5','Condition declarations'),conditions(step.condition_checks));
      item.append(el('h5','Limits'),list(step.limits),evidence(step.evidence));
      if(step.code_queries.length)item.append(el('p','Source queries: '+step.code_queries.join('; ')));
      steps.append(item);
    }
    detail.append(steps,el('p','Mechanisms were not executed by this plan. Review source rights, adapters and acceptance cases before a separate authorized experiment.','hint'));
    section.append(detail);
  }
  const unresolved=plan.unresolved;
  if(Object.values(unresolved).some(rows=>rows.length)){
    const detail=el('details');
    detail.append(el('summary','Unresolved inputs and excluded routes'),
      el('p',"These catalog branches may be unused by the retained options. Check each option's own input bindings and conditions.",'hint'));
    if(unresolved.missing_inputs_without_provider.length)detail.append(el('h4','Inputs with no declared provider'),list(unresolved.missing_inputs_without_provider));
    if(unresolved.unreachable_mechanisms.length)detail.append(el('h4','Unreachable mechanisms'),list(unresolved.unreachable_mechanisms.map(row=>row.mechanism+': requires '+row.missing_inputs.join(', '))));
    if(unresolved.cyclic_dependency_groups.length)detail.append(el('h4','Unseeded dependency cycles'),list(unresolved.cyclic_dependency_groups.map(group=>group.join(', '))));
    if(unresolved.incompatible_mechanisms.length){
      detail.append(el('h4','Excluded by a required condition'));
      for(const row of unresolved.incompatible_mechanisms)detail.append(el('p',row.mechanism),conditions(row.conditions));
    }
    detail.append(el('p','These diagnostics concern the supplied map. They are not a minimal repair plan or proof that all alternatives were searched.','hint'));
    section.append(detail);
  }
  const coverage=plan.coverage, detail=el('details');
  detail.append(el('summary','Search coverage and limits'));
  const fields=[
    ['Search performed','search_performed'],['Mechanisms supplied','mechanisms_supplied'],['Relevant mechanisms','relevant_mechanisms'],
    ['Eligible mechanisms','eligible_mechanisms'],['Maximum steps per route','max_steps'],['Maximum retained states','max_states'],
    ['Maximum displayed options','max_options'],['States retained','states_retained'],['States expanded','states_expanded'],
    ['Minimal complete sets found','minimal_plans_found'],['Options retained','options_retained'],['Options omitted','options_omitted'],
    ['Transitions omitted by state limit','transitions_omitted_by_state_budget'],['Transitions omitted by depth limit','transitions_omitted_by_depth'],
    ['Equivalent orders collapsed','equivalent_orders_collapsed'],['Redundant complete sets omitted','redundant_complete_sets_omitted'],
    ['State budget exhausted','state_budget_exhausted'],['Depth limited','depth_limited'],
    ['Search complete within depth bound','search_complete_within_depth'],['Declared reachable without depth limit','declared_reachable_without_depth_limit']
  ];
  const values=el('dl');
  for(const [title,key] of fields){
    if(!Object.hasOwn(coverage,key))continue;
    const value=coverage[key]===null?'Not assessed':typeof coverage[key]==='boolean'?(coverage[key]?'Yes':'No'):String(coverage[key]);
    values.append(el('dt',title),el('dd',value));
  }
  detail.append(values,el('p','Selection: '+label(coverage.selection_policy)),
    el('p','Inclusion minimality concerns mechanism sets for one declared output. It does not establish minimum effort, cost, operator intervention or market value.','hint'));
  section.append(detail);
  if(plan.limitations.length){const limits=el('details');limits.append(el('summary','Interpretation limits'),list(plan.limitations));section.append(limits);}
  parent.append(section);
}
async function show(id){const r=await api('/api/opportunity?run='+encodeURIComponent(id)),area=$('opp-detail');area.hidden=false;area.replaceChildren(el('h2',r.title),el('p',r.scope,'hint'));
for(const h of r.hypotheses){const a=el('article',undefined,'finding');a.append(el('p',label(h.state),'state'),el('h3',h.concept.title),el('p',h.concept.problem_hypothesis),el('p','Next action: '+label(h.concept.recommended_action)),el('p','User: '+h.concept.user),el('p',h.concept.proposed_intervention));
appendBuildOptions(a,h);
const table=el('table'),head=el('tr');for(const x of ['Mechanism','Missing prerequisites','Output fit'])head.append(el('th',x));table.append(head);for(const m of h.mechanisms){const tr=el('tr');tr.append(el('td',m.title),el('td',m.missing_prerequisites.join(', ')||'None in the declared map; not verified'),el('td',label(m.output_fit)));table.append(tr);}a.append(table);
a.append(el('h3','Challenge the opportunity'));for(const c of h.counter_searches)a.append(el('p',c.question));a.append(el('h3','Proposed experiment'),el('p',h.experiment.question),el('p','Experiment not executed. No business, rights or release approval.','hint'));
const detail=el('details');detail.append(el('summary','Source evidence and actual code leads'));for(const o of h.evidence.observations){detail.append(el('p',label(o.kind)+': '+o.statement));for(const e of o.checked_evidence)detail.append(el('p',e.source_uri+' · '+e.citation.quote,'code'));}for(const m of h.code_leads){detail.append(el('h4',m.mechanism));for(const c of m.candidates)detail.append(el('p',c.name+' · '+c.corpus+' · '+(c.definition_id||c.source_id),'code'));if(m.partial_matches_held_for_review)detail.append(el('p',m.partial_matches_held_for_review+' partial query matches excluded from the main shortlist.','hint'));appendReviewLeads(detail,m);}a.append(detail);
for(const decision of ['INVESTIGATE','REVISE','REJECT'])a.append(button(label(decision),async()=>{const reason=prompt('Explain your decision. This is feedback, not proof of validation.');if(!reason)return;await api('/api/opportunity/feedback',{run:id,note:{hypothesis:h.id,decision,reason,actor:'Local owner'}});say('Judgment retained without changing evidence or approval.');}));area.append(a);}return r;}
$('opp-demo').onclick=async()=>{try{await api('/api/opportunity/demo',{});say('Running the bounded, source-linked case study.');}catch(e){say(e.message);}};
$('opp-refresh').onclick=()=>refresh().catch(e=>say(e.message));
$('opp-import').onclick=async()=>{try{const f=$('opp-file').files[0];if(!f)throw Error('Choose a reviewed JSON brief.');if(f.size>384000)throw Error('Brief exceeds 384 KB.');const bundle=JSON.parse(await f.text());const r=await api('/api/opportunity/analyze',{bundle});await refresh();await show(r.id);say('Analyzed reviewed inputs. Results are hypotheses, not validated products.');}catch(e){say(e.message);}};
$('opp-note-form').onsubmit=async ev=>{ev.preventDefault();try{const source={id:'local-note',text:$('opp-note').value,kind:'firsthand',public:false,origin_group:'local-owner',uri:'conversation://local/workflow-note',observed_on:new Date().toISOString().slice(0,10),derived_from:[],capture_method:'verbatim_excerpt'};const r=await api('/api/opportunity/draft',{source});const a=$('opp-cues');a.replaceChildren();for(const x of r.suggestions)a.append(el('p',label(x.cue)+': '+x.evidence.quote));if(!r.suggestions.length)a.append(el('p','No supported lexical cues found. This is not evidence that no opportunity exists.'));say('Cues require a reviewed workflow map before opportunity analysis.');}catch(e){say(e.message);}};
async function poll(){try{const r=await api('/api/state');$('opp-operation').textContent=JSON.stringify(r.operation);$('opp-demo').disabled=r.operation.status==='RUNNING';if(r.operation.kind==='opportunity'&&r.operation.status==='FINISHED')await refresh();}catch(e){say(e.message);}}
refresh().catch(e=>say(e.message));setInterval(poll,2000);

$('opp-guided-form').onsubmit=async ev=>{ev.preventDefault();try{const r=await api('/api/opportunity/guided',{note:$('opp-note').value,actor:$('opp-actor').value,domain:$('opp-domain').value,received:$('opp-received').value,needed:$('opp-needed').value,pattern:$('opp-pattern').value,observation:$('opp-observation').value});await refresh();await show(r.id);say('Workflow hypothesis created from your explicit note and fields.');}catch(e){say(e.message);}};
