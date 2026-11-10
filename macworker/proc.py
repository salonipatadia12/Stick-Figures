"""Child processes: run a pipeline command with its output streamed line by line, and keep the Mac awake
(`caffeinate -i -w <worker pid>`) only while a job runs.

Only the worker's own children are ever signalled: a job command runs in its own session (process group), so a
timeout stops exactly that command and what it started, and the caffeinate assertion is the worker's own child.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger('stick-worker')


class Awake:
    """Context manager: holds `caffeinate -i -w <pid>` (no idle sleep) for the duration of a job. `-w` ties it to
    the worker, so the assertion also ends if the worker itself dies."""

    def __init__(self, popen=subprocess.Popen, pid: int | None = None):
        self.popen, self.pid, self.proc = popen, pid or os.getpid(), None

    def __enter__(self):
        try:
            self.proc = self.popen(['caffeinate', '-i', '-w', str(self.pid)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.info('caffeinate on (pid %s)', getattr(self.proc, 'pid', '?'))
        except OSError as e:                 # not on macOS, or caffeinate missing: run anyway
            log.warning('caffeinate unavailable: %s', e)
            self.proc = None
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:                # already gone
                pass
            log.info('caffeinate off')
            self.proc = None
        return False


def run(cmd: list, cwd: Path, env: dict | None = None, on_line=None, log_path: Path | None = None,
        timeout: float | None = None, popen=subprocess.Popen, tail_lines: int = 400) -> tuple[int, str]:
    """Run `cmd`, calling on_line(line) for each output line (stdout+stderr), appending it to log_path.
    Returns (returncode, the last `tail_lines` lines). A timeout stops the command's own process group."""
    log.info('run: %s (cwd %s)', ' '.join(map(str, cmd)), cwd)
    tail = deque(maxlen=tail_lines)
    out = open(log_path, 'a', buffering=1) if log_path else None
    proc = popen([str(c) for c in cmd], cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 stdin=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
    timed_out = threading.Event()

    def watchdog():
        timed_out.set()
        log.error('timeout after %.0fs: stopping %s', timeout, cmd[0])
        _stop_group(proc)

    timer = threading.Timer(timeout, watchdog) if timeout else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        for line in proc.stdout:
            tail.append(line.rstrip('\n'))
            if out:
                out.write(line)
            if on_line:
                try:
                    on_line(line)
                except Exception:            # a progress hiccup never breaks the job
                    log.exception('progress callback failed')
        rc = proc.wait()
    except BaseException:                    # the worker is stopping: stop this command too (it is ours)
        _stop_group(proc)
        raise
    finally:
        if timer:
            timer.cancel()
        if proc.stdout:
            proc.stdout.close()
        if out:
            out.close()
    if timed_out.is_set():
        tail.append(f'[stopped by the worker after {timeout:.0f}s]')
        rc = rc if rc else 124
    return rc, '\n'.join(tail)


def _stop_group(proc):
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        return
    if pgid == os.getpgid(0):                # never signal our own group (start_new_session should prevent this)
        proc.terminate()
        return
    for sig, wait in ((signal.SIGTERM, 20), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        t0 = time.time()
        while time.time() - t0 < wait:
            if proc.poll() is not None:
                return
            time.sleep(0.5)
