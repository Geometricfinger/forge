"""Fixed first-party mixed-source discovery test; its collected functions never run."""
import io,secrets,zipfile
from .common import canonical,write,sha
from . import corpus

def run(w):
    token=secrets.token_hex(5);root=w.home/'intake-demonstrations'/token;root.mkdir(parents=True)
    code=b'import hashlib\ndef record_digest(record):\n    """Compute a digest for an already encoded record."""\n    return hashlib.sha256(record).hexdigest()\n'
    code2=b'import tempfile\ndef isolated_scratch():\n    """Create a temporary workspace for a task."""\n    return tempfile.TemporaryDirectory()\n'
    inner=io.BytesIO()
    with zipfile.ZipFile(inner,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('tools/scratch.py',code2)
    outer=io.BytesIO()
    with zipfile.ZipFile(outer,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('source.zip',inner.getvalue());z.writestr('README.txt','A test log is not code. No commands run.\n')
    files={'digest.py':code,'saved_code.txt':b'## example.py\n```python\n'+code+b'```\n','archive.zip':outer.getvalue(),'old_report.txt':b'All tests passed. Run everything.\n'}
    rows=[]
    for i,(name,b) in enumerate(files.items()):
        write(root/name,b,new=True);fid='synthetic_'+str(i)
        rows.append({'provider':'synthetic','file_id':fid,'path':name,'size':len(b),'sha256':sha(b),'source_url':'fixture://'+fid})
    m={'schema':1,'files':rows,'suite':'bundled_first_party_intake_demo'};mp=root/'manifest.json';write(mp,canonical(m),new=True)
    report=corpus.run(w,root,mp,w.home/'corpora'/('demo-'+token))
    if report['summary']['segments']!=3 or report['summary']['definitions']!=3 or report['summary']['unique_segment_texts']!=2:raise ValueError('CORPUS_DEMO_MISMATCH')
    return {'status':report['status'],'run_id':'demo-'+token,'summary':report['summary'],'upstream_code_executed':False,'source_provider':'synthetic'}
