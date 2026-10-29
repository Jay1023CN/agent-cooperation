"""Blender export inspection using a controlled executable fixture."""

import hashlib
import json
import threading

import pytest

from arena import blender


def test_custom_blender_location_is_used_without_installing_anything(tmp_path, monkeypatch):
    executable = tmp_path / "Blender folder" / "blender"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(blender.shutil, "which", lambda _: None)
    assert blender.find_blender(str(executable)) == str(executable)
    assert blender.find_blender(str(tmp_path / "missing")) is None


def test_export_inspection_runs_blender_without_autorun_and_returns_evidence(tmp_path, monkeypatch):
    outputs = tmp_path / "arena-exports"
    outputs.mkdir()
    scene = outputs / "场景.blend"
    scene.write_bytes(b"example")
    monkeypatch.setattr(blender, "find_blender", lambda configured: "blender-fixture")
    calls = []

    def run(args, cwd, **kwargs):
        calls.append((args, cwd, kwargs))
        return "startup\nARENA_BLEND_CHECK:" + json.dumps({"version": "4.5", "scenes": 1, "objects": 3}) + "\n"

    result = blender.verify_exports(tmp_path, None, threading.Event(), run)
    assert result == {
        "state": "verified",
        "files": [
            {
                "name": "场景.blend",
                "sha256": hashlib.sha256(b"example").hexdigest(),
                "version": "4.5",
                "scenes": 1,
                "objects": 3,
            }
        ],
    }
    args, cwd, options = calls[0]
    assert cwd == tmp_path and args[-1] == str(scene.resolve())
    assert "--background" in args and "--disable-autoexec" in args and "--python-exit-code" in args
    assert options["timeout"] == 120


def test_missing_blender_and_missing_export_are_reported_without_false_success(tmp_path, monkeypatch):
    monkeypatch.setattr(blender, "find_blender", lambda configured: None)
    assert blender.verify_exports(tmp_path, None, threading.Event(), None)["state"] == "no_blend"
    folder = tmp_path / "arena-exports"
    folder.mkdir()
    (folder / "model.blend").write_bytes(b"project")
    assert blender.verify_exports(tmp_path, None, threading.Event(), None) == {
        "state": "unavailable",
        "files": ["model.blend"],
    }


@pytest.mark.parametrize("output", ["crashed", 'ARENA_BLEND_CHECK:{"version":"4.5","scenes":-1,"objects":1}'])
def test_invalid_blender_response_is_not_accepted(tmp_path, monkeypatch, output):
    folder = tmp_path / "arena-exports"
    folder.mkdir()
    (folder / "broken.blend").write_bytes(b"invalid")
    monkeypatch.setattr(blender, "find_blender", lambda configured: "blender-fixture")
    with pytest.raises(blender.BlenderCheckError, match="broken.blend"):
        blender.verify_exports(tmp_path, None, threading.Event(), lambda *args, **kwargs: output)


def test_project_changed_during_inspection_is_not_verified(tmp_path, monkeypatch):
    folder = tmp_path / "arena-exports"
    folder.mkdir()
    scene = folder / "scene.blend"
    scene.write_bytes(b"before")
    monkeypatch.setattr(blender, "find_blender", lambda configured: "blender-fixture")

    def run(*args, **kwargs):
        scene.write_bytes(b"after")
        return 'ARENA_BLEND_CHECK:{"version":"4.5","scenes":1,"objects":1}'

    with pytest.raises(blender.BlenderCheckError, match="changed during inspection"):
        blender.verify_exports(tmp_path, None, threading.Event(), run)
