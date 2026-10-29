"""Inspect exported Blender projects with the Blender installed on the worker."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

MARKER = "ARENA_BLEND_CHECK:"
CHECK = (
    "import bpy,sys,json; "
    "r=bpy.ops.wm.open_mainfile(filepath=sys.argv[-1],load_ui=False); "
    "assert 'FINISHED' in r, 'Blender did not open the project'; "
    "print('ARENA_BLEND_CHECK:'+json.dumps({'version':bpy.app.version_string,"
    "'scenes':len(bpy.data.scenes),'objects':len(bpy.data.objects)}))"
)


class BlenderCheckError(RuntimeError):
    pass


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_blender(configured=None):
    """Use a configured executable, PATH, or a standard installation location."""
    if configured:
        return shutil.which(configured) or (
            str(Path(configured).expanduser().resolve()) if Path(configured).expanduser().is_file() else None
        )
    binary = shutil.which("blender")
    if binary:
        return binary
    candidates = [Path("/Applications/Blender.app/Contents/MacOS/Blender")]
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        candidates.extend(
            sorted((Path(program_files) / "Blender Foundation").glob("Blender */blender.exe"), reverse=True)
        )
    return next((str(path) for path in candidates if path.is_file()), None)


def verify_exports(workspace, configured, cancel, run):
    """Open each exported .blend without saving it; return explicit evidence for the reviewer."""
    output = Path(workspace) / "arena-exports"
    if not output.exists():
        return {"state": "no_blend", "files": []}
    if output.is_symlink():
        raise BlenderCheckError("Blender export directory cannot be a symbolic link")
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
            raise BlenderCheckError("Blender export paths must remain inside arena-exports")
        if path.is_file() and path.suffix.lower() == ".blend":
            files.append(path)
            if len(files) > 30 or path.stat().st_size > 128 * 1024 * 1024:
                raise BlenderCheckError("Blender exports exceed the artifact count or size limit")
    if not files:
        return {"state": "no_blend", "files": []}
    executable = find_blender(configured)
    if not executable:
        return {"state": "unavailable", "files": [str(path.relative_to(output)) for path in files]}
    allowed = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG"}
    environment = {
        key: value for key, value in os.environ.items() if key.upper() in allowed or key.upper().startswith("LC_")
    }
    results = []
    for path in files:
        name = str(path.relative_to(output))
        try:
            digest = file_digest(path)
            text = run(
                [
                    executable,
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                    "--python-exit-code",
                    "1",
                    "--python-expr",
                    CHECK,
                    "--",
                    str(path.resolve()),
                ],
                Path(workspace),
                env=environment,
                timeout=120,
                cancel=cancel,
            )
            markers = [line[len(MARKER) :] for line in text.splitlines() if line.startswith(MARKER)]
            data = json.loads(markers[-1]) if len(markers) == 1 else None
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("version"), str)
                or not all(type(data.get(key)) is int and data[key] >= 0 for key in ("scenes", "objects"))
            ):
                raise ValueError("Blender did not return a valid inspection result")
            if file_digest(path) != digest:
                raise ValueError("Blender project changed during inspection")
        except Exception as exc:
            # The caller preserves cancellation and records a failure before dispatching review.
            if cancel.is_set():
                raise
            raise BlenderCheckError(f"无法用 Blender 打开 {name}：{exc}") from exc
        results.append({"name": name, "sha256": digest, **data})
    return {"state": "verified", "files": results}
