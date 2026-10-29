"""Optional same-user Claude inbox transport; never launches a model or controls UI.

Claude documents the inbox, but its frame and registry formats are not a stable
public API. Keep this adapter opt-in and require an explicit session and cwd.
Only the published peer key is used, never the session's own-child token.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from pathlib import Path

from .handoff_store import HandoffError


class ClaudePreflightError(HandoffError):
    """The notification failed before any connection or write was attempted."""


def _json_file(path: Path, maximum: int = 65536, private: bool = False) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise HandoffError("Invalid Claude registry file")
    if os.name != "nt" and (info.st_uid != os.getuid() or (private and info.st_mode & 0o077)):
        raise HandoffError("Claude registry ownership or permissions do not match")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise HandoffError("Claude registry file exceeds the limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise HandoffError("Invalid Claude registry record")
    return value


def _process_start(pid: int) -> str:
    if os.name != "nt":
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=2, check=True
        )
        return result.stdout.strip()
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise HandoffError("Claude session process is unavailable")
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
            raise HandoffError("Cannot verify Claude session process")
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _write_pipe(path: str, data: bytes) -> None:
    """Bound both connecting and writing; a successful write is not a delivery receipt."""
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(Overlapped),
    ]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Overlapped),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(Overlapped)]
    if not kernel.WaitNamedPipeW(path, 2000):
        raise HandoffError("Claude inbox pipe is unavailable")
    handle = kernel.CreateFileW(path, 0x40000000, 0, None, 3, 0x40000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise HandoffError("Cannot connect to Claude inbox pipe")
    event = kernel.CreateEventW(None, True, False, None)
    if not event:
        kernel.CloseHandle(handle)
        raise HandoffError("Cannot create Claude inbox write event")
    operation = Overlapped(hEvent=event)
    buffer = ctypes.create_string_buffer(data)
    try:
        written = wintypes.DWORD()
        if not kernel.WriteFile(handle, buffer, len(data), None, ctypes.byref(operation)):
            if ctypes.get_last_error() != 997:
                raise HandoffError("Claude inbox write failed; inspect before retrying")
            if kernel.WaitForSingleObject(event, 2000) != 0:
                kernel.CancelIoEx(handle, ctypes.byref(operation))
                kernel.GetOverlappedResult(handle, ctypes.byref(operation), ctypes.byref(written), True)
                raise HandoffError("Claude inbox write timed out; inspect before retrying")
        if not kernel.GetOverlappedResult(handle, ctypes.byref(operation), ctypes.byref(written), True):
            raise HandoffError("Claude inbox write failed; inspect before retrying")
        if written.value != len(data):
            raise HandoffError("Incomplete Claude inbox write; inspect before retrying")
    finally:
        kernel.CloseHandle(event)
        kernel.CloseHandle(handle)


class ClaudePeer:
    def __init__(self, registry: Path | None = None):
        config = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        self.registry = registry or config / "sessions"

    def _load(self, path: Path) -> dict:
        record = _json_file(path)
        pid = record.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1 or path.name != f"{pid}.json":
            raise HandoffError("Invalid Claude session process")
        if type(record.get("peerProtocol")) is not int or record["peerProtocol"] != 1:
            raise HandoffError("Unsupported Claude peer protocol")
        if not isinstance(record.get("sessionId"), str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", record["sessionId"]
        ):
            raise HandoffError("Invalid Claude session ID")
        if not isinstance(record.get("cwd"), str) or not Path(record["cwd"]).is_absolute():
            raise HandoffError("Invalid Claude session workspace")
        if _process_start(pid) != str(record.get("procStart")):
            raise HandoffError("Claude session process changed")
        endpoint = record.get("messagingSocketPath", "")
        if not isinstance(endpoint, str) or not endpoint:
            raise HandoffError("Claude session has no inbox")
        if os.name == "nt":
            if not isinstance(endpoint, str) or not re.fullmatch(r"\\\\\.\\pipe\\LOCAL\\cc-msg-[0-9a-f]{32}", endpoint):
                raise HandoffError("Unsupported Claude inbox pipe")
        else:
            address = Path(endpoint)
            if not address.is_absolute() or address.is_symlink():
                raise HandoffError("Invalid Claude inbox socket")
            info, parent = address.stat(), address.parent.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise HandoffError("Claude inbox owner does not match")
            if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o022:
                raise HandoffError("Claude inbox directory is not private")
        return record

    def list_sessions(self) -> list[dict]:
        sessions = []
        for path in sorted(self.registry.glob("*.json"))[:256]:
            try:
                record = self._load(path)
                sessions.append(
                    {key: record.get(key) for key in ("sessionId", "cwd", "name", "status", "version", "entrypoint")}
                )
            except (OSError, ValueError, subprocess.SubprocessError, HandoffError):
                continue
        return sessions

    def send(self, session_id: str, expected_cwd: str, text: str, message_id: str) -> dict:
        try:
            endpoint, data = self._prepare(session_id, expected_cwd, text, message_id)
        except (OSError, ValueError, subprocess.SubprocessError, HandoffError) as exc:
            raise ClaudePreflightError(str(exc)) from exc
        if os.name == "nt":
            _write_pipe(endpoint, data)
        else:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(endpoint)
                connection.sendall(data)
        return {"state": "transport_written", "session_id": session_id, "message_id": message_id, "delivered": False}

    def _prepare(self, session_id: str, expected_cwd: str, text: str, message_id: str) -> tuple[str, bytes]:
        if not text.strip() or len(text.encode("utf-8")) > 8192:
            raise HandoffError("Claude notification must contain 1 to 8192 bytes")
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", message_id):
            raise HandoffError("Invalid Claude notification ID")
        matches = []
        for path in sorted(self.registry.glob("*.json"))[:256]:
            try:
                record = self._load(path)
            except (OSError, ValueError, subprocess.SubprocessError, HandoffError):
                continue
            if record["sessionId"] == session_id:
                matches.append(record)
        if len(matches) != 1:
            raise HandoffError("Select exactly one verified live Claude session")
        target = matches[0]
        if (
            not Path(expected_cwd).is_absolute()
            or Path(target.get("cwd", "")).resolve() != Path(expected_cwd).resolve()
        ):
            raise HandoffError("Claude session workspace does not match expected_cwd")
        endpoint = target["messagingSocketPath"]
        canonical = os.path.normcase(endpoint) if os.name == "nt" else str(Path(endpoint).resolve())
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        key = _json_file(self.registry / f"{target['pid']}.{digest}.key", 4096, private=True)
        start = key.get("procStartFt") if os.name == "nt" else key.get("procStart")
        if str(start) != str(target["procStart"]) or key.get("pidDomain") != target.get("pidDomain"):
            raise HandoffError("Claude inbox key belongs to a different process")
        token = key.get("peerToken", "")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
            raise HandoffError("Claude inbox peer key is invalid")
        # No permission-class assertion or own-child credential: receiver policy applies.
        frames = [
            {"type": "auth", "token": token},
            {"type": "user", "msgV": 1, "msg_id": message_id, "message": {"role": "user", "content": text}},
        ]
        data = ("\n".join(json.dumps(frame, ensure_ascii=False) for frame in frames) + "\n").encode("utf-8")
        return endpoint, data
