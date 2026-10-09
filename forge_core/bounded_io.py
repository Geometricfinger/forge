"""Bounded stdout/stderr collection for FORGE's fixed, trusted tools.

Both streams count against one byte allowance *while* the tool is running.
Only stdout is retained; diagnostics are counted, never included in exceptions.
This is resource supervision, not a filesystem/network or hostile-code sandbox.
POSIX children share a new process group for failure cleanup. On Windows only
its direct child is terminated; Windows descendant containment is unqualified.
"""
from __future__ import annotations
import math
import os
import signal
import subprocess
import threading
import time
from .common import Blocked

MAX_OUTPUT_BYTES = 8_000_000
# Patch: an explicit, larger ceiling that only a caller can opt into
# (the corpus parser). It matches mixed_worker.py's own 16 MB RESULT_LIMIT plus
# envelope room; the default for every other fixed tool is unchanged.
MAX_PARSER_OUTPUT_BYTES = 17_000_000
MAX_INPUT_BYTES = 8_000_000
CHUNK_BYTES = 65_536
CLEANUP_SECONDS = 2.0

class _Capture:
    """Shared budget; retain at most `limit` stdout bytes across both readers."""
    def __init__(self, limit: int):
        self.limit = limit
        self.consumed = 0
        self.output = bytearray()
        self.overflow = threading.Event()
        self.failed = threading.Event()
        self.lock = threading.Lock()

    def accept(self, data: bytes, stdout: bool) -> None:
        with self.lock:
            room = max(0, self.limit - self.consumed)
            self.consumed += len(data)
            if stdout and room:
                self.output.extend(data[:room])
            if self.consumed > self.limit:
                self.overflow.set()


def _kill(process: subprocess.Popen) -> None:
    """Kill the original POSIX group even if its immediate parent has exited."""
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Patch: macOS/BSD killpg() returns EPERM (not ESRCH)
            # when the group's only remaining member is an unreaped zombie
            # leader. Nothing in the group can still run; fall back to the
            # direct child so cleanup completes instead of raising.
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
    elif process.poll() is None:
        process.kill()


def run_bounded(cmd, home, env, timeout=40, input=None, output_limit=None):
    """Run an explicit argument vector; return bounded stdout or raise Blocked.

    Timeouts include I/O completion after the child exits. Process creation and
    a bounded cleanup interval are additional OS-dependent overhead. No shell,
    automatic retries, dependency installation, or permission expansion occurs.
    """
    if (type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= 600):
        raise Blocked('TOOL_TIMEOUT_BOUNDS')
    if input is not None and (not isinstance(input, bytes) or len(input) > MAX_INPUT_BYTES):
        raise Blocked('TOOL_INPUT_BOUNDS')
    if (not isinstance(cmd, (list, tuple)) or not 1 <= len(cmd) <= 100
            or any(not isinstance(x, str) or '\x00' in x for x in cmd)
            or not cmd[0]):
        raise Blocked('TOOL_ARGUMENTS')
    if output_limit is None:
        output_limit = MAX_OUTPUT_BYTES
    if type(output_limit) is not int or not 1 <= output_limit <= MAX_PARSER_OUTPUT_BYTES:
        raise Blocked('TOOL_OUTPUT_BOUNDS')
    capture = _Capture(output_limit)
    stopping = threading.Event()
    output_done = threading.Event()
    error_done = threading.Event()
    input_done = threading.Event()
    started = time.monotonic()
    process = subprocess.Popen(
        cmd, cwd=home, env=env, shell=False, bufsize=0,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=os.name == 'posix',
    )
    threads = []

    def drain(stream, keep, done):
        try:
            while not stopping.is_set():
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    break
                capture.accept(chunk, keep)
                if capture.overflow.is_set():
                    break
        except (OSError, ValueError):
            if not stopping.is_set():
                capture.failed.set()
        finally:
            stream.close()
            done.set()

    def feed():
        try:
            remaining = memoryview(input)
            while remaining and not stopping.is_set():
                written = process.stdin.write(remaining[:CHUNK_BYTES])
                if not written:
                    capture.failed.set()
                    break
                remaining = remaining[written:]
        except BrokenPipeError:
            # A fixed tool may intentionally stop without consuming all input.
            pass
        except (OSError, ValueError):
            if not stopping.is_set():
                capture.failed.set()
        finally:
            process.stdin.close()
            input_done.set()

    succeeded = False
    try:
        for target, args, name in (
            (drain, (process.stdout, True, output_done), 'stdout'),
            (drain, (process.stderr, False, error_done), 'stderr'),
        ):
            t = threading.Thread(target=target, args=args, name='forge-tool-'+name, daemon=True)
            t.start(); threads.append(t)
        if input is not None:
            t = threading.Thread(target=feed, name='forge-tool-stdin', daemon=True)
            t.start(); threads.append(t)
        else:
            input_done.set()
        while True:
            if capture.overflow.is_set():
                raise Blocked('TOOL_OUTPUT_LIMIT')
            if capture.failed.is_set():
                raise Blocked('TOOL_IO_FAILED')
            if output_done.is_set() and error_done.is_set() and input_done.is_set() and process.poll() is not None:
                break
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise Blocked('TOOL_TIMEOUT')
            time.sleep(min(.01, remaining))
        # Readers are finished; recheck flags after the completion barrier.
        # Otherwise a flag set between the earlier checks and poll() could
        # return a truncated result as success.
        if capture.overflow.is_set():
            raise Blocked('TOOL_OUTPUT_LIMIT')
        if capture.failed.is_set():
            raise Blocked('TOOL_IO_FAILED')
        if process.returncode:
            raise Blocked('TOOL_EXIT_' + str(process.returncode).replace('-', 'NEG'))
        succeeded = True
        return bytes(capture.output)
    finally:
        stopping.set()
        if not succeeded:
            _kill(process)
        deadline = time.monotonic() + CLEANUP_SECONDS
        try:
            process.wait(timeout=max(.01, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill(process)
            raise Blocked('TOOL_CLEANUP_FAILED')
        for t in threads:
            t.join(max(0, deadline-time.monotonic()))
        if any(t.is_alive() for t in threads):
            # An escaped descendant may hold a pipe. Never report completion.
            raise Blocked('TOOL_CLEANUP_FAILED')
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
