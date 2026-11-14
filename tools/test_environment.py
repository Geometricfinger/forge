"""Use physical temporary paths for local qualification fixtures.

macOS commonly exposes TMPDIR through /var or /tmp symlinks. Resolve the
test harness's temporary root without weakening product path validation.
"""
import os
from pathlib import Path
import tempfile


def prepare_test_environment():
    root = str(Path(tempfile.gettempdir()).resolve(strict=True))
    # tempfile caches the selected directory; children also need the same root.
    tempfile.tempdir = root
    os.environ['TMPDIR'] = root
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
