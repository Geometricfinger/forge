"""One integrated opportunity case study with actual corpus retrieval over bundled demo sources."""
from .common import canonical,read,write,sha
from .engine import ROOT
from .opportunities import OpportunityStore
from .opportunity_examples import live_case_study
from . import corpus

DEMO_SOURCES=ROOT/'examples/demo/opportunity_sources'
RUN_ID='opportunity-demo'


def demo_corpus(w):
    """Build (once per workspace) a corpus over the bundled synthetic demo sources."""
    out=w.home/'corpora'/RUN_ID
    if (out/'corpus.json').exists():return corpus.get(w,RUN_ID)
    root=w.home/'intake-demonstrations'/RUN_ID;rows=[]
    for i,p in enumerate(sorted(DEMO_SOURCES.glob('*.py'))):
        b=read(p,250_000);write(root/p.name,b);fid='demo_'+str(i)
        rows.append({'provider':'synthetic','file_id':fid,'path':p.name,'size':len(b),'sha256':sha(b),'source_url':'fixture://'+fid})
    mp=root/'manifest.json';write(mp,canonical({'schema':1,'files':rows,'suite':'bundled_opportunity_demo'}))
    corpus.run(w,root,mp,out)
    return corpus.get(w,RUN_ID)


def run(w):
    w.guard();bundle=live_case_study()
    corpora=[('demo-sources',demo_corpus(w))]
    book=OpportunityStore(w);result=book.run(bundle,corpora)
    return {'status':'OPPORTUNITY_CASE_STUDY_COMPLETED','run':result['id'],
            'summary':result['summary'],'corpora':['demo-sources'],
            'source_mode':'Synthetic operator note, selected public excerpts and bundled demo sources; not fresh crawling.',
            'model_calls':0,'source_code_executed':False,'market_validated':False}
