"""Synthetic demo source for the research runner. Fake data only; never executed."""
import json
import subprocess


def run_with_timeout(command, seconds):
    """Run a fixed command list as a subprocess with a timeout; the caller handles TimeoutExpired."""
    return subprocess.run(command, capture_output=True, timeout=seconds, check=False)


def save_checkpoint(path, source_id, state):
    """Write a checkpoint record for one source so an interrupted job can resume."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'source': source_id, 'state': state}, f, sort_keys=True)


def load_checkpoint(path):
    """Read a previously saved source checkpoint; a missing file means start over."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def request_manual_approval(opportunity):
    """Queue an opportunity for manual approval; nothing is approved automatically."""
    return {'opportunity': opportunity, 'status': 'AWAITING_MANUAL_APPROVAL'}
