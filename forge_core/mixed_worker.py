"""Fixed parser worker. Only supplied text is parsed; no collected imports execute."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if sys.platform.startswith('linux'):
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(768*1024**2,768*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
from forge_core.common import loads,canonical
from forge_core.mixed_intake import DEFAULTS,inspect_container
try:
    if sys.argv[1:]:
        # This fixed mode carries only the container bytes over stdin. The
        # original JSON protocol remains available to existing trusted callers.
        if len(sys.argv)!=3 or sys.argv[1]!='--raw-container':
            raise ValueError('WORKER_ARGUMENTS')
        limit=DEFAULTS['max_container_bytes']
        raw=sys.stdin.buffer.read(limit+1)
        if len(raw)>limit:raise ValueError('INPUT_LIMIT')
        result=inspect_container(raw,sys.argv[2])
    else:
        raw=sys.stdin.buffer.read(17_000_001)
        if len(raw)>17_000_000:raise ValueError('INPUT_LIMIT')
        obj=loads(raw)
        if set(obj)!={'data_hex','name','limits'}:raise ValueError('FIELDS')
        result=inspect_container(bytes.fromhex(obj['data_hex']),obj['name'],obj['limits'])
    data=canonical(result)
    if len(data)>16_000_000:raise ValueError('RESULT_LIMIT')
    sys.stdout.buffer.write(data)
except Exception as e:
    sys.stdout.buffer.write(canonical({'status':'BLOCKED','reason':str(e) if type(e) is ValueError else type(e).__name__}))
    sys.exit(2)
