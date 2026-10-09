"""Select the next evidence task before generating more code.

A rule-based research agenda, not economic value-of-information estimation,
novelty clearance, automatic source acquisition, or authority to contact users.
Inputs are internally generated Opportunity Lab hypotheses.
"""
from .common import canonical, sha


def next_investigation(hypothesis):
    state=hypothesis['state']; need=hypothesis['need']; tasks=[]
    def add(kind,question,evidence,falsifier):
        row={'kind':kind,'question':question,'required_evidence':evidence,
             'falsifier':falsifier,'state':'NOT_STARTED',
             'source_acquisition_authorized':False,'execution_authorized':False}
        row['id']='investigate_'+sha(canonical([hypothesis['id'],kind,question]))[:24]
        tasks.append(row)
    if state=='EXISTING_OPTION_TO_VERIFY':
        add('existing_solution_fit','Does the existing option satisfy this exact actor, handoff and environment?',
            ['Versioned feature evidence','A representative task with the existing option'],
            'The current option solves the task adequately; another product is unnecessary.')
    elif state in {'CONFLICT_REQUIRES_REVIEW','COUNTEREVIDENCE_REVIEW'}:
        add('resolve_conflict','Are the conflicting claims about the same task, user group, version and conditions?',
            ['Independent source records','Explicit scope comparison'],
            'The apparent contradiction is a scope mismatch or refutes the proposed problem.')
    elif state=='STALE_EVIDENCE_RECHECK':
        add('refresh_evidence','Does the problem or missing capability still occur?',
            ['Fresh permitted observation','Publication and observation dates'],
            'An update or changed workflow has removed the problem.')
    else:
        add('observe_workflow','Where does a real operator lose time, information or correctness in this handoff?',
            ['Observed task sequence','Current workaround','Counterexample completing the task without the intervention'],
            'The required information already arrives reliably or the intervention adds more burden than it removes.')
        if state=='SINGLE_SOURCE_PROBLEM':
            add('independent_observation','Does the problem recur outside the first source or originating account?',
                ['Separately attributable task observations','Explicit source relationship review'],
                'The reports are copies of one incident or the problem is limited to a configuration already fixable.')
    if state not in {'EXISTING_OPTION_TO_VERIFY','CONFLICT_REQUIRES_REVIEW','COUNTEREVIDENCE_REVIEW','STALE_EVIDENCE_RECHECK'}:
        missing=sorted({v for m in hypothesis['mechanisms'] for v in m['missing_prerequisites']})
        if missing:
            add('prerequisite','Can the proposed mechanism obtain these missing inputs: '+', '.join(missing)+'?',
                ['Feasible acquisition route','Representative ambiguous or missing-input case'],
                'The signal is unavailable or cannot distinguish the alternatives; the mechanism must change or abstain.')
        add('existing_solution_fit','What existing tool, service, manual workaround or adjacent-domain method already addresses the task?',
            ['Direct capability evidence','Documented limitations','Observed fit for the target task'],
            'An existing solution meets the requirement at acceptable effort.')
    add('bounded_comparison','What smallest comparison would decide whether to retain or reject the proposed intervention?',
        ['Frozen baseline and task inputs','Correct and incorrect outcomes','Operator effort','Integration and operating burden'],
        'There is no useful advantage, a mandatory behavior regresses, or the apparent gain does not repeat.')
    return {'schema':1,'status':'PLAN_ONLY','policy':'EVIDENCE_GAPS_BEFORE_CODE_VOLUME',
            'tasks':tasks,'next_task':tasks[0]['id'],'dependencies_inferred_not_runtime_proven':True,
            'cost_model':'NOT_ESTIMATED','estimated_success_probability':None,
            'execution_authorized':False,'network_requests':0,'market_validated':False,
            'novelty_established':False,'independent_evaluation':False,
            'limitations':['Rules propose questions; they do not perform or validate research.',
                           'A missing prerequisite is not a proof that no alternative mechanism exists.']}
