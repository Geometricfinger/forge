"""FORGE Hound 0.5: bounded, source-bound static hunt; never imports target code.

Recognizes selected syntax and local value-flow only. No runtime correctness,
whole-program scale guarantee, copy-depth proof, rights clearance or novelty score.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import baseline_sniffers as base

VERSION = '0.5'
RIGID = 'geometry.rigid_icp_controls'
SCALE = 'geometry.scale_receiver_lineage'
ABSTAIN = 'identification.explicit_nondecision'
PROFILE_IDS = (RIGID, SCALE, ABSTAIN, 'validation.explicit_units_finite_values', 'storage.resolved_path_guard')


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_digest() -> str:
    return sha(Path(__file__).read_bytes() + Path(base.__file__).read_bytes() + Path(__file__).with_name('flow_core.py').read_bytes() + Path(__file__).with_name('library_rules.py').read_bytes() + Path(__file__).with_name('script_scope.py').read_bytes() + (Path(__file__).parent/'profiles'/'baseline.json').read_bytes())


# Kept in a separate module to make inference rules independently reviewable.
from flow_core import LocalFlow, Value, UNKNOWN, join, PRIMITIVES, DIMENSION, SCALED_MATRIX
import library_rules
import script_scope
PROFILE_IDS = (*PROFILE_IDS, PRIMITIVES, DIMENSION, SCALED_MATRIX, library_rules.OBJECT_FACTOR)


def scan_bytes(data:bytes,*,source_id:str)->dict:
    if not isinstance(data,bytes) or len(data)>base.MAX_SOURCE_BYTES:raise ValueError('SOURCE_BYTE_LIMIT')
    if not isinstance(source_id,str) or not source_id or len(source_id)>2000:raise ValueError('INVALID_SOURCE_ID')
    tree=ast.parse(data,filename='<approved-source>')
    if sum(1 for _ in ast.walk(tree))>base.MAX_NODES:raise ValueError('AST_NODE_LIMIT')
    import_context=library_rules.resolve_relative_imports(tree,source_id)
    results=[];count=0;digest=sha(data)
    legacy=base.read_profiles(Path(__file__).with_name('profiles')/'baseline.json')
    module_writes={base.dotted(n).split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Attribute) and isinstance(n.ctx,(ast.Store,ast.Del)) and base.dotted(n)}
    for name,fn in base.functions(tree):
        count+=1;flow=LocalFlow(tree,fn,name,module_writes=module_writes,library=library_rules);flow.sequence(fn.body,flow.env)
        library_rules.add_object_factor_context(tree,fn,name,flow)
        # Keep the earlier unit and path detectors as compatibility profiles.
        aliases={k:v.value for k,v in flow.env.items() if v.kind=='import'}
        for profile in legacy[1:2]:
            for match in base.DETECTORS[profile['detector']](fn,aliases,profile):
                flow.findings.append({'profile_id':profile['id'],'qualified_name':name,
                    'function_lines':[fn.lineno,fn.end_lineno],'limitations':profile['limitations'],
                    'inherited_detector_version':base.VERSION,'runtime_verified':False,**match})
        for r in flow.findings:
            r.update(source_id=source_id,source_sha256=digest,detector_version=VERSION)
            r['finding_id']='finding_'+sha(canonical(r));results.append(r)
    return {'source_id':source_id,'source_sha256':digest,'functions_inspected':count,
        'evidence_level':'STATIC_ONLY','source_body_retained':False,'findings':results,'import_context':import_context,
        'script_findings':script_scope.inspect(tree,data,source_id,VERSION,library_rules)}


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--file',type=Path);p.add_argument('--source-id')
    a=p.parse_args()
    try:
        if a.worker:
            packet=json.loads(sys.stdin.buffer.read(base.MAX_SOURCE_BYTES*3+1))
            result=scan_bytes(bytes.fromhex(packet['source_hex']),source_id=packet['source_id'])
        else:
            if not a.file or not a.source_id:p.error('--file and --source-id required')
            from atlas_hunt import bounded_read, inspect_isolated
            result=inspect_isolated(bounded_read(a.file,base.MAX_SOURCE_BYTES),a.source_id)
        print(json.dumps(result,indent=2,allow_nan=False));return 0
    except (ValueError,SyntaxError,OSError,RecursionError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'INSPECTION_FAILED','error_type':type(exc).__name__}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
