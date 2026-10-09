"""First-party reviewed fixtures and a source-bound case study, never a market census."""
from copy import deepcopy
from .common import sha,canonical


def source(key, content, kind='firsthand', public=False, family=None, uri=None):
    return {'id':key,'text':content,'kind':kind,'public':public,'origin_group':family or key,
            'capture_method':'synthetic_fixture' if kind=='synthetic' else 'attributed_summary',
            'uri':uri or 'fixture://'+key,'observed_on':'2026-09-21','derived_from':[]}

def cite(src, excerpt=None):
    excerpt = src['text'] if excerpt is None else excerpt
    start=src['text'].index(excerpt)
    return {'source':src['id'],'start':start,'end':start+len(excerpt),'quote':excerpt}

def example_bundle():
    """Synthetic fixture with stated demand and an unfulfilled handoff."""
    s=source('operator','I must manually recover the artifact identity before reviewing the received file.',kind='synthetic')
    return {'schema':1,'title':'Trace the missing identity','as_of':'2026-09-21','max_age_days':365,
            'public_brief':'Investigate artifact identity at a workflow handoff.',
            'sources':[s],
            'workflows':[{'id':'handoff','title':'Artifact handoff','actor':'Receiving operator','domain':'engineering',
                          'purpose':'Review a received artifact without guessing its identity.',
                          'initial_fields':['artifact','attributes'],
                          'steps':[{'id':'export','action':'Export a file','requires':['artifact'],'produces':['file'], 'evidence':[cite(s)]},
                                   {'id':'review','action':'Review the file','requires':['file','identity'],'produces':['decision'],'evidence':[cite(s)]}],
                          'needs':[{'id':'identity','step':'review','field':'identity','pattern':'identity_loss',
                                    'why':'The next step needs an identity that the described handoff does not supply.',
                                    'basis':'reported','evidence':[cite(s)]}]}],
            'observations':[{'id':'manual','workflow':'handoff','need':'identity','kind':'manual','scope':'target',
                             'statement':'The fixture operator reconstructs identity manually.','evidence':[cite(s)]}],
            'mechanisms':[{'id':'matching','title':'Attribute matching with abstention','domain':'catalog records',
                           'purpose':'Narrow identity candidates','mechanism':'Compare recorded attributes and escalate indistinguishable results.',
                           'addresses':['identity_loss'],'requires':['attributes','distinguishing_information'],
                           'provides':['identity_candidates'],'limits':['Identical recorded attributes cannot determine one unique identity.'],
                           'evidence':[cite(s)],'code_queries':['match_catalog_entry','normalize_part_label']},
                          {'id':'record','title':'Carry an identity record','domain':'software provenance',
                           'purpose':'Preserve identity across a handoff','mechanism':'Bind an acquisition record to an artifact digest.',
                           'addresses':['identity_loss'],'requires':['artifact'],
                           'provides':['identity_evidence'],'limits':['A digest binds bytes, not the correctness of the supplied identity.'],
                           'evidence':[cite(s)],'code_queries':['file_hash','digest']}],
            'preferences':{'identity_loss':3,'manual_bridge':3,'verification_gap':3,'information_gap':1}}


def live_case_study():
    """Neutral, fully synthetic case study plus two short public documentation excerpts.

    The operator report is a synthetic fixture, not a real user or market statement.
    Code leads come from the bundled demo sources in examples/demo/opportunity_sources.
    """
    b=example_bundle();b['title']='Example opportunity discovery: identity and evidence handoffs'
    s=source('operator_report','Our team re-identifies received parts by hand before review. Several part variants look alike and some are indistinguishable.',kind='firsthand',uri='fixture://operator-report')
    # Attributed summary of a synthetic operator note; not a verbatim external quote.
    s['kind']='firsthand';b['sources']=[s]
    w=b['workflows'][0];w['title']='Part identity handoff';w['actor']='Receiving operator';w['domain']='parts workflow'
    w['steps'][0]['evidence']=[cite(s)];w['steps'][1]['evidence']=[cite(s)]
    w['needs'][0]['evidence']=[cite(s)];w['needs'][0]['basis']='inferred'
    b['observations'][0]['kind']='pain';b['observations'][0]['statement']='The synthetic operator reports a difficult part-identity workflow; prevalence is unverified.';b['observations'][0]['evidence']=[cite(s)]
    b['mechanisms'][0]['code_queries']=['match_catalog_entry','normalize_part_label']
    for m in b['mechanisms']:m['evidence']=[cite(s)]
    # Published sources are quotations under 25 words each, read in this build.
    gh=source('github_docs','artifact attestations are not a guarantee that an artifact is secure.',kind='product_docs',public=True,
              family='github',uri='https://docs.github.com/en/actions/concepts/security/artifact-attestations')
    ml=source('mlflow_docs','Each run records metadata',kind='product_docs',public=True,
              family='mlflow',uri='https://mlflow.org/docs/latest/ml/tracking')
    restore=source('code_review','The demo recovery module checks file hashes and SQLite integrity; startup is not executed by verify_snapshot.',kind='source_review',
                   uri='fixture://demo/recovery.py')
    receipt=source('receipt_review','The demo inspector checks run identity and record digest, without replaying writes.',kind='source_review',
                   uri='fixture://demo/records.py')
    units=source('units_review','The demo process-reference validator requires explicit units and finite values; its snapshot labels declarations unverified.',kind='source_review',
                 uri='fixture://demo/recovery.py#validate_reference')
    gh['capture_method']=ml['capture_method']='verbatim_excerpt'
    b['sources'] += [gh,ml,restore,receipt,units]
    def workflow(key,title,field,evidence):
        return {'id':key,'title':title,'actor':'Developer','domain':'software evidence','purpose':title,
                'initial_fields':['artifact','record'],
                'steps':[{'id':'produce','action':'Produce an artifact','requires':['artifact'],'produces':['output'],'evidence':[cite(evidence)]},
                         {'id':'consume','action':'Evaluate output for use','requires':['output',field],'produces':['decision'],'evidence':[cite(evidence)]}],
                'needs':[{'id':field,'step':'consume','field':field,'pattern':'verification_gap','why':'A separately required decision is not established by the inspected mechanism.',
                          'basis':'inferred','evidence':[cite(evidence)]}]}
    b['workflows'] += [workflow('attestation','Separate provenance from correctness','behavior_evidence',gh),
                       workflow('recovery','Separate intact backup from working application','startup_evidence',restore),
                       workflow('tracking','Avoid reinventing run metadata tracking','run_metadata',ml)]
    b['observations'] += [
        {'id':'provenance_limit','workflow':'attestation','need':'behavior_evidence','kind':'unknown','scope':'target','statement':'Documentation warns that provenance is not security proof.','evidence':[cite(gh)]},
        {'id':'backup_limit','workflow':'recovery','need':'startup_evidence','kind':'unknown','scope':'target','statement':'Source review does not establish startup readiness or demand for another backup product.','evidence':[cite(restore)]},
        {'id':'existing_tracking','workflow':'tracking','need':'run_metadata','kind':'supported','scope':'target','statement':'MLflow documents recording run metadata; investigate fit before proposing another tracker.','evidence':[cite(ml)]}
    ]
    b['mechanisms'] += [
        {'id':'receipt','title':'Evidence receipt inspection','domain':'measurement software','purpose':'Check a stored run record',
         'mechanism':'Compare identity and digest without replaying operations.',
         'addresses':['verification_gap'],'requires':['record'],'provides':['integrity_evidence'],
         'limits':['A matching receipt is not an authenticated proof of truthful execution or application correctness.'],
         'evidence':[cite(receipt)],'code_queries':['inspect_run_receipt','Snapshot.resolve']},
        {'id':'snapshot','title':'Snapshot integrity plus separate startup trial','domain':'desktop recovery','purpose':'Investigate recovery readiness',
         'mechanism':'Verify a backup, then propose a separately authorized disposable startup experiment.',
         'addresses':['verification_gap'],'requires':['artifact'],'provides':['integrity_evidence'],
         'limits':['The startup evaluator and runtime environment are additional work, not supplied by a hash check.'],
         'evidence':[cite(restore)],'code_queries':['verify_snapshot','restore_profile']},
        {'id':'typed','title':'Typed declaration contract','domain':'process records','purpose':'Preserve interpretation across handoffs',
         'mechanism':'Carry explicit units, reference versions and unknown-verification status.',
         'addresses':['identity_loss','information_gap'],'requires':['record'],'provides':['context'],
         'limits':['A declared parameter is not a verified machine measurement.'],
         'evidence':[cite(units)],'code_queries':['validate_reference','build_process_snapshot']}
    ]
    return b
