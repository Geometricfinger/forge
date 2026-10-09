"""Synthetic demo module: run receipts and file digests. Fake data only."""
import hashlib, json

def file_hash(data):
    """Return the SHA-256 digest of already loaded file bytes."""
    return hashlib.sha256(data).hexdigest()

def inspect_run_receipt(receipt, record):
    """Compare a stored receipt with a run record without replaying any operation."""
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return {'run_matches': receipt.get('run') == record.get('run'), 'digest_matches': receipt.get('digest') == digest}

class Snapshot:
    def __init__(self, entries):
        self.entries = dict(entries)

    def resolve(self, name):
        """Resolve a recorded entry by name; unknown names stay unknown."""
        return self.entries.get(name)
