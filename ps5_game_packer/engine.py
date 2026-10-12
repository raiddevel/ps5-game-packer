# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 raiddevel
"""Running MkPFS: the pack command, the subprocess, pausing and the write throttle. The password travels in
MKPFS_ARCHIVE_PASSWORD, never on the command line."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import environment
from .analysis import job_password


PAUSE_MAX = 15.0  # longest stretch, in seconds, that the throttle keeps MkPFS stopped
NICE = 10  # CPU priority of MkPFS (0 normal ... 19 lowest)
BELOW_NORMAL_PRIORITY_CLASS = 0x4000  # Windows priority class "below normal"


class EngineError(RuntimeError):
    """The installed engine cannot do this job (no --layer or no --archive-root)."""


def _layer_spec(layer: dict) -> str:
    return f"{layer['path']}::{layer.get('subpath', '') or ''}"


def pack_command(job: dict, output: Path, options: dict) -> list[str]:
    """MkPFS command for `job` with `options` (level, cpu, verify, temp_dir)."""
    layers = [c for c in job.get("layers", []) if c.get("active", True)]
    common = ["--compression-level", str(options["level"]), "--cpu-count", str(options["cpu"]),
              "--temp-folder", str(options["temp_dir"])]
    if options.get("verify"):
        common.append("--verify")
    cmd = [str(environment.MKPFS), "pack", "folder", "--no-adjust-output-file-extension", *common]
    if layers:
        if not any(c.get("complete") for c in layers):
            raise EngineError("no_base")
        if not environment.mkpfs_merges_layers():
            raise EngineError("no_layers")
        base, rest = layers[0], layers[1:]
        if base.get("virtual"):
            if not environment.mkpfs_reads_archives():
                raise EngineError("no_archives")
            cmd += ["--archive-root", base.get("subpath", "")]
        for c in rest:
            if c.get("virtual") and not environment.mkpfs_reads_archives():
                raise EngineError("no_archives")
            cmd += ["--layer", _layer_spec(c)]
        return cmd + [str(base["path"]), str(output)]
    if job.get("virtual"):
        if not environment.mkpfs_reads_archives():
            raise EngineError("no_archives")
        cmd += ["--archive-root", job.get("subpath", "")]
    return cmd + [str(job["path"]), str(output)]


def two_pass_commands(job: dict, output: Path, options: dict) -> tuple[list[str], list[str], Path]:
    """Legacy method for a plain folder: (step 1 to an uncompressed temporary PFS, step 2, temporary file)."""
    temp_dat = Path(options["temp_dir"]) / f"{job['title_id']}_pfs_image.dat"
    base = [str(environment.MKPFS)]
    common = ["--compression-level", str(options["level"]), "--cpu-count", str(options["cpu"]),
              "--temp-folder", str(options["temp_dir"])]
    if options.get("verify"):
        common.append("--verify")
    step1 = base + ["pack", "folder", "--raw", "--no-compress", "--no-adjust-output-file-extension",
                    "--temp-folder", str(options["temp_dir"]), str(job["path"]), str(temp_dat)]
    step2 = base + ["pack", "file", *common, str(temp_dat), str(output)]
    return step1, step2, temp_dat


def allows_two_pass(job: dict) -> bool:
    """The two-pass method (--raw) only works for a plain folder without layers."""
    return not job.get("virtual") and not any(c.get("active", True) for c in job.get("layers", [])[1:]) \
        and not (job.get("layers") and job["layers"][0].get("virtual"))


def low_priority_command(cmd: list[str]) -> list[str]:
    """Prepends `ionice -c 3` (Linux) and `nice -n 10` when available. Both exec: the PID is still MkPFS's."""
    if environment.IS_WINDOWS:
        return list(cmd)
    prefix: list[str] = []
    if environment.IS_LINUX and shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    if shutil.which("nice"):
        prefix += ["nice", "-n", str(NICE)]
    return prefix + list(cmd)


def _lower_priority() -> None:
    """preexec_fn for when there is no `nice`."""
    try:
        os.setpriority(os.PRIO_PROCESS, 0, NICE)
    except Exception:
        pass


def start(cmd: list[str], cwd: Path, password: str = "") -> subprocess.Popen:
    """Starts MkPFS in its own process group, without a window and at low priority."""
    extra: dict = {}
    if environment.IS_WINDOWS:
        extra["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                  | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                  | BELOW_NORMAL_PRIORITY_CLASS)
    else:
        extra["start_new_session"] = True
        if "nice" not in low_priority_command(cmd)[:4]:
            extra["preexec_fn"] = _lower_priority
    return subprocess.Popen(low_priority_command(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, cwd=str(cwd), env=environment.engine_env(password), **extra)


def kill(proc: subprocess.Popen | None, wait: float = 5.0) -> None:
    """Ends MkPFS and its child processes (the compressors); if it is still there after `wait` s, forcibly."""
    if proc is None or proc.poll() is not None:
        return
    if environment.IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           **environment.quiet_flags())
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    def finish_off() -> None:
        t0 = time.time()
        while time.time() - t0 < wait:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass

    threading.Thread(target=finish_off, daemon=True).start()


def can_pause() -> bool:
    """On Windows, psutil is needed to suspend and resume a process."""
    if not environment.IS_WINDOWS:
        return True
    try:
        import psutil
        return True
    except ImportError:
        return False


def _stop_or_continue(proc: subprocess.Popen, stop: bool) -> bool:
    """Stops (True) or resumes (False) the MkPFS process, and only that one. Its children get no signal: unrar
    loses data if it receives SIGSTOP while writing to the pipe."""
    try:
        if environment.IS_WINDOWS:
            import psutil
            p = psutil.Process(proc.pid)
            p.suspend() if stop else p.resume()
        else:
            os.kill(proc.pid, signal.SIGSTOP if stop else signal.SIGCONT)
        return True
    except Exception:
        return False


class Pause:
    """Pause of a running MkPFS, shared by the "Pause" button and the throttle. The process stays stopped while
    either flag is set; `release()` before killing it."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._lock = threading.Lock()
        self.user = False
        self.throttle = False

    @property
    def stopped(self) -> bool:
        return self.user or self.throttle

    def set(self, who: str, value: bool) -> bool:
        """Sets or clears the 'user' or 'throttle' flag. Returns False if the signal could not be sent."""
        if who not in ("user", "throttle"):
            raise ValueError(who)
        with self._lock:
            before = self.stopped
            setattr(self, who, bool(value))
            if before == self.stopped or self.proc.poll() is not None:
                return True
            return _stop_or_continue(self.proc, self.stopped)

    def release(self) -> None:
        """Clears both flags and resumes the process (before cancelling or closing the window)."""
        with self._lock:
            if not self.stopped:
                return
            self.user = self.throttle = False
            if self.proc.poll() is None:
                _stop_or_continue(self.proc, False)


def throttle(proc: subprocess.Popen, output: Path, stop: threading.Event, pause: Pause | None = None) -> None:
    """Linux only: flushes the output file to disk and pauses MkPFS when the kernel piles up too many pending
    writes (otherwise every program that writes freezes). The flushing runs in its own thread because it blocks."""
    if not environment.IS_LINUX:
        stop.wait()
        return
    if pause is None:
        pause = Pause(proc)
    import glob

    def dirty_mb() -> int:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("Dirty:"):
                        return int(line.split()[1]) // 1024
        except OSError:
            pass
        return 0

    def syncer() -> None:
        while not stop.wait(1.0):
            for f in glob.glob(str(output) + "*"):
                try:
                    fd = os.open(f, os.O_RDONLY)
                    try:
                        os.fdatasync(fd)
                        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                    finally:
                        os.close(fd)
                except OSError:
                    pass

    threading.Thread(target=syncer, daemon=True).start()
    paused = False
    paused_since = 0.0
    resumed_at = 0.0
    previous_size = _output_size(output)
    # Pause when the dirty pages pass 768 MB and our file is growing; resume below 256 MB or after PAUSE_MAX s
    # (the dirty pages belong to the whole system and may not be ours).
    while not stop.wait(0.2):
        if proc.poll() is not None:
            continue
        dirty = dirty_mb()
        now = time.time()
        size = _output_size(output)
        growing = size > previous_size
        previous_size = size
        try:
            if not paused and dirty > 768 and growing and now - resumed_at > 2.0:
                pause.set("throttle", True)
                paused = True
                paused_since = now
            elif paused and (dirty < 256 or now - paused_since > PAUSE_MAX):
                pause.set("throttle", False)
                paused = False
                resumed_at = now
        except Exception:
            pass
    if paused:
        pause.set("throttle", False)


def _output_size(output: Path) -> int:
    import glob

    total = 0
    for f in glob.glob(str(output) + "*"):
        try:
            total += os.path.getsize(f)
        except OSError:
            pass
    return total


def read_lines(proc: subprocess.Popen):
    """Yields the output lines of MkPFS (split on \\n or \\r, to see the progress bar)."""
    assert proc.stdout is not None
    pending = b""
    while True:
        chunk = proc.stdout.read1(8192)
        if not chunk:
            break
        pending += chunk
        while True:
            i = -1
            for sep in (b"\n", b"\r"):
                k = pending.find(sep)
                if k != -1 and (i == -1 or k < i):
                    i = k
            if i == -1:
                break
            yield pending[:i].decode("utf-8", "replace")
            pending = pending[i + 1:]
    if pending:
        yield pending.decode("utf-8", "replace")


def failure_cause(lines: list[str]) -> str | None:
    """Why MkPFS failed, from its last lines: 'space' (no room at the destination; MkPFS deletes its .tmp),
    'password' (RAR/ZIP password) or None if unknown."""
    tail = " ".join(lines[-40:]).lower()
    if any(p in tail for p in ("no space left", "errno 28", "not enough space", "disk full", "enospc")):
        return "space"
    if "password" in tail:
        return "password"
    return None


def pack(job: dict, output: Path, options: dict, password: str = "", on_line=None) -> tuple[int, list[str]]:
    """Packs a job from start to end without a window (tests and --auto). Returns (exit code, lines).

    The password is the job's own (the one that scanned its archives) or, failing that, `password`.
    """
    password = job_password(job) or password
    lines: list[str] = []
    commands: list[tuple[list[str], Path]]
    temp_dat: Path | None = None
    if options.get("two_pass") and allows_two_pass(job):
        step1, step2, temp_dat = two_pass_commands(job, output, options)
        commands = [(step1, temp_dat), (step2, output)]
    else:
        commands = [(pack_command(job, output, options), output)]
    code = 0
    try:
        for cmd, target in commands:
            proc = start(cmd, output.parent, password)
            stop = threading.Event()
            threading.Thread(target=throttle, args=(proc, target, stop, Pause(proc)), daemon=True).start()
            try:
                for line in read_lines(proc):
                    lines.append(line)
                    if on_line is not None:
                        on_line(line)
            finally:
                stop.set()
            code = proc.wait()
            if code != 0:
                break
    finally:
        if temp_dat is not None:
            try:
                temp_dat.unlink(missing_ok=True)
            except OSError:
                pass
    return code, lines
