"""Tiny example module for the Forge manifest walkthrough (MIT, synthetic)."""
import hashlib
import subprocess


def record_digest(record: bytes) -> str:
    """Return the SHA-256 hex digest of an already encoded record."""
    return hashlib.sha256(record).hexdigest()


def list_files(folder: str):
    """List a folder with ls (Forge records this call; it never runs it)."""
    return subprocess.run(["ls", folder], capture_output=True, check=False)
