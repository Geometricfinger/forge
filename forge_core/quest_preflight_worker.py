"""Fixed compiler worker for exact packaged Python bytes. Never runs that code."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform.startswith('linux'):
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

from forge_core.common import canonical
from forge_core.quest_preflight import inspect_module

if len(sys.argv) != 2:
    raise SystemExit(2)
raw = sys.stdin.buffer.read(2_000_001)
if len(raw) > 2_000_000:
    raise SystemExit(2)
sys.stdout.buffer.write(canonical(inspect_module(raw, sys.argv[1])))
