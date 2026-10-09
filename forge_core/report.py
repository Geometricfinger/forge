"""Private, escaped, offline reports. Never include source bodies or tokens."""
import html

def render(m):
    esc=lambda x:html.escape(str(x),quote=True)
    cards=[]
    for c in m['candidates']:
        rows=''.join('<li><b>'+esc(f.get('resolved_api',''))+'</b> · '+esc(f.get('qualified_name',f.get('scope_name','source')))+'<p>'+esc(f.get('capability',''))+' · source observation, not runtime verification</p></li>' for f in c['findings'])
        cards.append('<article><h2>'+esc(c['repository'])+'</h2><p>'+esc(c['path'])+'</p><p class="muted">Commit '+esc(c['commit'])+' · '+esc(c['rights_status'])+'</p><ul>'+rows+'</ul></article>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FORGE mission evidence</title><style>body{font:16px/1.6 system-ui;background:#10141e;color:#eef2f9;max-width:1050px;margin:40px auto;padding:0 24px}h1{font-size:38px}article{padding:24px;border:1px solid #354057;margin:20px 0;border-radius:12px}.muted{color:#b5c2d9;overflow-wrap:anywhere}li{margin:12px 0}b{color:#9fded2}</style><header><p>FORGE / PRIVATE MISSION EVIDENCE</p><h1>'+esc(m['title'])+'</h1><p>'+esc(m['status'])+'</p><p>'+esc(m['public_brief'])+'</p></header><p>'+str(m['requests_used'])+' request attempts · '+str(len(m['candidates']))+' source observations · no execution or release approval</p>'+''.join(cards)+'<h2>Coverage boundaries</h2><p>'+esc(m['scope'])+'</p>'+''.join('<p>'+esc(g)+'</p>' for g in m['gaps'])+'<p class="muted">The model did not run independently. API labels are search requirements, not demonstrated correctness. Private metadata only.</p></html>'
