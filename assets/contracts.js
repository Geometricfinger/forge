'use strict';
let token=location.hash.slice(1)||sessionStorage.getItem('forge-token')||'';
history.replaceState(null,'',location.pathname);if(token)sessionStorage.setItem('forge-token',token);
const $=id=>document.getElementById(id);
function notice(text){$('contract-notice').textContent=text;}
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.error||'Request failed');return data;}
async function refresh(){const x=await api('/api/state');$('contract-operation').textContent=JSON.stringify(x.operation,null,2);$('contract-run').disabled=x.operation.status==='RUNNING';}
$('fingerprint-form').onsubmit=async event=>{event.preventDefault();try{const r=await api('/api/interop/fingerprint',{raw_json:$('raw-json').value});$('fingerprint-result').textContent=JSON.stringify(r,null,2);notice('Receipt created. It describes content under the declared numeric policy; it does not authenticate a sender.');}catch(e){notice(e.message);}};
$('contract-run').onclick=async()=>{try{await api('/api/contract-trial',{});notice('Running the fixed integration checks; use Refresh status to read the result.');await refresh();}catch(e){notice(e.message);}};
$('contract-refresh').onclick=()=>refresh().catch(e=>notice(e.message));
api('/api/integration-contract').then(x=>{$('contract-description').textContent=JSON.stringify(x,null,2);}).catch(e=>notice(e.message));refresh().catch(e=>notice(e.message));
