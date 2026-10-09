"""Synthetic demo module: match received items against catalog records. Fake data only."""

def normalize_part_label(label):
    """Normalize a free-text part label to a lowercase token."""
    return '-'.join(label.strip().lower().split())

def match_catalog_entry(attributes, catalog):
    """Return catalog ids whose recorded attributes all match; abstain (None) when several remain."""
    hits = [cid for cid, rec in sorted(catalog.items()) if all(rec.get(k) == v for k, v in attributes.items())]
    if len(hits) != 1:
        return None if hits else []
    return hits

def attribute_overlap(a, b):
    """Fraction of shared keys with equal values; abstains (None) when no keys are shared."""
    shared = set(a) & set(b)
    if not shared:
        return None
    return sum(a[k] == b[k] for k in shared) / len(shared)
