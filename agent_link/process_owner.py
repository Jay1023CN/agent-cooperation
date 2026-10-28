"""Own subprocess descendants so terminal release also stops background children."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

LOADER_ENVIRONMENT = ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LIBPATH")


def _supervisor_environment(environment):
    env = dict(os.environ if environment is None else environment)
    for name in LOADER_ENVIRONMENT:
        env[f"_PAL_SUPERVISOR_HAS_{name}"] = "1" if name in env else "0"
        env[f"_PAL_SUPERVISOR_ORIGINAL_{name}"] = env.get(name, "")
    return env


def _child_environment():
    env = dict(os.environ)
    for name in LOADER_ENVIRONMENT:
        present = env.pop(f"_PAL_SUPERVISOR_HAS_{name}", "0")
        original = env.pop(f"_PAL_SUPERVISOR_ORIGINAL_{name}", "")
        if present == "1":
            env[name] = original
        else:
            env.pop(name, None)
        # PyInstaller's bootloader may set these while launching the supervisor.
        env.pop(name + "_ORIG", None)
    for name in list(env):
        if name.startswith("_PAL_SUPERVISOR_"):
            env.pop(name)
    return env


class WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("min_memory", ctypes.c_size_t),
                ("max_memory", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class IOCounts(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IOCounts),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = [wintypes.HANDLE]
        self.api.ResumeThread.restype = wintypes.DWORD
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("Cannot create a supervised process job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError("Cannot configure a supervised process job")

    def assign_and_resume(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("Cannot assign the subprocess to its supervision job")
        threads = psutil.Process(process.pid).threads()
        if not threads:
            raise OSError("Suspended subprocess has no primary thread")
        for thread in threads:
            handle = self.api.OpenThread(0x0002, False, thread.id)  # THREAD_SUSPEND_RESUME
            if not handle:
                raise OSError("Cannot resume the supervised subprocess")
            try:
                if self.api.ResumeThread(handle) == 0xFFFFFFFF:
                    raise OSError("Cannot resume the supervised subprocess")
            finally:
                self.api.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


class PosixOwnedProcess:
    """Expose the child's status while keeping its process-group leader alive."""

    def __init__(self, supervisor, status_fd, control_fd):
        self._arena_supervisor = supervisor
        self._arena_control_fd = control_fd
        self._arena_stop_lock = threading.Lock()
        self._arena_stopped = False
        self._arena_ready = threading.Event()
        self._arena_done = threading.Event()
        self._arena_start_error = False
        self._arena_started = False
        self._arena_status_known = False
        self.returncode = None
        self.pid = supervisor.pid
        self.args = supervisor.args
        self.stdin, self.stdout, self.stderr = supervisor.stdin, supervisor.stdout, supervisor.stderr

        def read_status():
            try:
                with os.fdopen(status_fd, "r", encoding="ascii", errors="replace") as source:
                    for line in source:
                        if line == "READY\n":
                            self._arena_started = True
                            self._arena_ready.set()
                        elif line == "ERROR\n":
                            self._arena_start_error = True
                            self._arena_ready.set()
                        elif line.startswith("EXIT "):
                            self.returncode = int(line[5:].strip())
                            self._arena_status_known = True
                            break
            except (OSError, ValueError):
                pass
            finally:
                # An unknown exit must never look like a successful command.
                if self.returncode is None:
                    self.returncode = -1
                if not self._arena_started:
                    self._arena_start_error = True
                self._arena_ready.set()
                self._arena_done.set()

        self._arena_status_thread = threading.Thread(target=read_status, daemon=True)
        self._arena_status_thread.start()

    def poll(self):
        return self.returncode if self._arena_done.is_set() else None

    def wait(self, timeout=None):
        if not self._arena_done.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        return self.returncode


def _supervisor_command(status_fd, control_fd, command):
    if getattr(sys, "frozen", False):
        prefix = [sys.executable, "internal-supervisor"]
    else:
        prefix = [sys.executable, str(Path(__file__).resolve()), "--supervisor"]
    return [*prefix, str(status_fd), str(control_fd), *command]


def supervise(status_fd, control_fd, command):
    """Keep the group ID reserved until cleanup, including after its child exits."""
    signal.signal(signal.SIGTERM, lambda *_: None)

    def terminate_group():
        # The supervisor still owns the group ID during the entire escalation.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgrp(), signal.SIGTERM)
        time.sleep(0.5)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgrp(), signal.SIGKILL)

    try:
        child = subprocess.Popen(command, stdin=0, stdout=1, stderr=2, close_fds=True, env=_child_environment())
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            os.write(status_fd, b"ERROR\n")
        return 127
    try:
        os.write(status_fd, b"READY\n")
    except OSError:
        # The owner may disappear after the child starts but before READY.
        terminate_group()
        raise
    for fd in (0, 1, 2):
        with contextlib.suppress(OSError):
            os.close(fd)

    def parent_lost():
        try:
            while os.read(control_fd, 4096):
                pass
        except OSError:
            pass
        terminate_group()

    try:
        threading.Thread(target=parent_lost, daemon=True).start()
    except Exception:
        terminate_group()
        raise
    result = child.wait()
    with contextlib.suppress(OSError):
        os.write(status_fd, f"EXIT {result}\n".encode("ascii"))
        os.close(status_fd)
    while True:
        time.sleep(3600)


def _valid_pid(process):
    return (
        isinstance(process.pid, int)
        and not isinstance(process.pid, bool)
        and process.pid > 1
        and (os.name == "nt" or process.pid != os.getpgrp())
    )


def spawn_owned(command, **kwargs):
    job = WindowsJob() if os.name == "nt" else None
    raw = None
    process = None
    group_confirmed = False
    fds = []
    try:
        if job:
            # Assign before any program code runs, so rapidly spawned children are also owned.
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
            raw = subprocess.Popen(command, **kwargs)
        else:
            status_read, status_write = os.pipe()
            control_read, control_write = os.pipe()
            fds = [status_read, status_write, control_read, control_write]
            kwargs["start_new_session"] = True
            kwargs["pass_fds"] = (status_write, control_read)
            kwargs["env"] = _supervisor_environment(kwargs.get("env"))
            raw = subprocess.Popen(_supervisor_command(status_write, control_read, command), **kwargs)
        if not _valid_pid(raw):
            raise RuntimeError("Subprocess did not provide a safe, independent process group")
        if job:
            raw._arena_stop_lock = threading.Lock()
            raw._arena_job = job
            raw._arena_stopped = False
            job.assign_and_resume(raw)
            return raw
        try:
            group = os.getpgid(raw.pid)
        except ProcessLookupError:
            raise RuntimeError("Could not verify the subprocess process group") from None
        if group != raw.pid:
            raise RuntimeError("Subprocess did not start in its own process group")
        group_confirmed = True
        os.close(status_write)
        os.close(control_read)
        fds.remove(status_write)
        fds.remove(control_read)
        process = PosixOwnedProcess(raw, status_read, control_write)
        fds.clear()  # ownership moved to the wrapper and its status thread
        if not process._arena_ready.wait(10) or process._arena_start_error:
            raise RuntimeError("Supervised subprocess could not start")
        return process
    except BaseException:
        if job:
            job.close()
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        if raw:
            if not job and group_confirmed and raw.poll() is None:
                with contextlib.suppress(OSError):
                    os.killpg(raw.pid, signal.SIGKILL)
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                raw.kill()
                raw.wait(timeout=2)
            for stream in (raw.stdin, raw.stdout, raw.stderr):
                if stream:
                    stream.close()
        if process:
            with contextlib.suppress(OSError):
                os.close(process._arena_control_fd)
            process._arena_status_thread.join(timeout=2)
        raise


def stop_owned(process):
    with process._arena_stop_lock:
        if process._arena_stopped:
            return
        if os.name == "nt":
            process._arena_job.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        else:
            supervisor = process._arena_supervisor
            if not _valid_pid(process) or supervisor.poll() is not None:
                raise RuntimeError("Supervisor exited before process-group cleanup; outcome is unknown")
            # The still-unreaped supervisor reserves its PID and group ID until
            # both signals have been sent. Never poll/reap it during this window.
            os.killpg(process.pid, signal.SIGTERM)
            time.sleep(0.5)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            supervisor.wait(timeout=2)
            process.wait(timeout=2)
            with contextlib.suppress(OSError):
                os.close(process._arena_control_fd)
            process._arena_status_thread.join(timeout=2)
            if not process._arena_status_known:
                raise RuntimeError("Supervised subprocess status was lost; outcome is unknown")
        process._arena_stopped = True


if __name__ == "__main__" and len(sys.argv) >= 5 and sys.argv[1] == "--supervisor":
    raise SystemExit(supervise(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]))
