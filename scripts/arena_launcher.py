"""Single entry point for the native PAL Arena distribution."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys


def configure_output():
    """Use the same protocol/log encoding for redirected Windows and Unix output."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def external_environment():
    """Keep bundled libraries out of unrelated programs launched by the worker."""
    env = os.environ.copy()
    if not getattr(sys, "frozen", False):
        return env
    if sys.platform.startswith("linux"):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    if "DYLD_LIBRARY_PATH_ORIG" in env:
        env["DYLD_LIBRARY_PATH"] = env.pop("DYLD_LIBRARY_PATH_ORIG")
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        bundled = os.path.normcase(os.path.abspath(bundled))
        paths = []
        for path in env.get("PATH", "").split(os.pathsep):
            try:
                inside = os.path.commonpath([bundled, os.path.normcase(os.path.abspath(path))]) == bundled
            except ValueError:
                inside = False
            if not inside:
                paths.append(path)
        env["PATH"] = os.pathsep.join(paths)
    return env


def run_external(argv):
    """Isolate Windows loader changes from concurrent worker threads."""
    if not argv:
        print("internal-exec requires a program and its arguments", file=sys.stderr)
        return 2
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        import ctypes

        if not ctypes.windll.kernel32.SetDllDirectoryW(None):
            raise ctypes.WinError()
    try:
        return subprocess.call(argv, env=external_environment(), shell=False)
    except OSError as exc:
        print(f"Cannot start external program: {exc}", file=sys.stderr)
        return 127


def run_stdio(entry):
    """Protect interpreter streams from SDK wrappers closing their shared buffers."""
    original_input, original_output = sys.stdin, sys.stdout
    with (
        os.fdopen(os.dup(original_input.fileno()), "r", encoding="utf-8", errors="replace") as reader,
        os.fdopen(os.dup(original_output.fileno()), "w", encoding="utf-8") as writer,
    ):
        try:
            sys.stdin, sys.stdout = reader, writer
            entry()
        finally:
            sys.stdin, sys.stdout = original_input, original_output


def main():
    if getattr(sys, "frozen", False):
        configure_output()
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if len(sys.argv) == 1:
        sys.argv.append(command)
    if command == "internal-exec":
        raise SystemExit(run_external(sys.argv[2:]))
    if command == "internal-handoff-client":
        from agent_link.handoff_client import main as client_main

        sys.argv.pop(1)
        client_main()
        return
    if command == "internal-supervisor":
        from agent_link.process_owner import supervise

        if len(sys.argv) < 5:
            raise SystemExit("internal-supervisor requires status, control and command arguments")
        raise SystemExit(supervise(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]))
    if command == "worker":
        from arena.worker import main as entry

        sys.argv.pop(1)
    elif command == "maintenance":
        from arena.maintenance import main as entry

        sys.argv.pop(1)
    elif command == "internal-mcp":
        from arena.mcp import main as entry

        sys.argv.pop(1)
    elif command == "internal-agent-link":
        from agent_link.server import main as entry

        sys.argv.pop(1)
    elif command == "internal-desktop-mcp":
        from arena.desktop_mcp import main as entry

        sys.argv.pop(1)
    elif command == "internal-handoff":
        from agent_link.desktop_handoff import main as entry

        sys.argv.pop(1)
    else:
        from arena.cli import main as entry
    if command in ("internal-mcp", "internal-agent-link", "internal-desktop-mcp", "internal-handoff") and getattr(
        sys, "frozen", False
    ):
        return run_stdio(entry)
    entry()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
