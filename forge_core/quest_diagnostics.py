"""Actionable quest validation without echoing supplied field contents."""
from .common import Blocked, text


class DiagnosticBlocked(Blocked):
    """Keep existing machine-readable reasons and add a safe field location."""
    def __init__(self, code, field, hint):
        super().__init__(code)
        self.detail = {'field': field, 'hint': hint}


def require_text(value, field, limit=2000):
    try:
        return text(value, limit)
    except Blocked as exc:
        raise DiagnosticBlocked(str(exc), field,
                                f'Use nonempty text of at most {limit} characters without control characters.') from exc
