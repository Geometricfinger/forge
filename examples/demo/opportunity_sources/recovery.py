"""Synthetic demo module: backup checks and typed declarations. Fake data only."""
import hashlib, sqlite3

def verify_snapshot(path, expected_sha256):
    """Check a backup file digest and SQLite integrity; does not start any application."""
    with open(path, 'rb') as f:
        ok = hashlib.sha256(f.read()).hexdigest() == expected_sha256
    con = sqlite3.connect(path)
    try:
        return ok and con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        con.close()

def restore_profile(snapshot_dir, profile_name):
    """Return the path a profile would be restored to; restoring itself is a separate step."""
    return f'{snapshot_dir}/{profile_name}.profile'

def validate_reference(value, unit):
    """Require an explicit unit and a finite value for a declared reference parameter."""
    if not unit or value != value or value in (float('inf'), float('-inf')):
        raise ValueError('declared reference needs a unit and a finite value')
    return {'value': value, 'unit': unit, 'verified': False}

def build_process_snapshot(declarations):
    """Bundle typed declarations; every entry stays labelled as unverified."""
    return [validate_reference(d['value'], d['unit']) for d in declarations]
