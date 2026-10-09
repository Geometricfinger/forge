"""Fixed worker: parse an approved source; never import the inspected implementation."""
from pathlib import Path
import argparse,sys
sys.dont_write_bytecode=True
if sys.platform.startswith('linux'):
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(12,12))
    resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
# The module directory is explicitly added despite isolated interpreter mode.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from safeio import strict_json,canonical

def main():
    p=argparse.ArgumentParser();p.add_argument('--addon',type=Path,required=True);p.add_argument('--hound',type=Path,required=True);a=p.parse_args()
    packet=strict_json(sys.stdin.buffer.read(900_001))
    sys.path.insert(0,str(a.addon/'tool'))
    import mission_probe as probe
    data=bytes.fromhex(packet['source_hex'])
    result=probe.scan(data,packet['source_id'],packet['profile'],a.hound)
    probe.validate_result(result,data,packet['source_id'],packet['profile'],a.hound)
    sys.stdout.buffer.write(canonical(result))
if __name__=='__main__':
    try: main()
    except Exception as e:
        print(canonical({'status':'BLOCKED','error_type':type(e).__name__}).decode(),file=sys.stderr)
        raise SystemExit(2)
