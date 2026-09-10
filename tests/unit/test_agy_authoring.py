# Copyright 2026 Google LLC

"""Unit tests for the isolated Antigravity SDK bridge."""

from __future__ import annotations

import io
import json
import os

import pytest

from app import agy_authoring


def test_runtime_paths_expose_only_bundled_skills_and_project(monkeypatch, tmp_path):
    monkeypatch.delenv("SLIDEGEN_AGY_SKILLS_PATHS", raising=False)
    monkeypatch.delenv("SLIDEGEN_AGY_WORKSPACES", raising=False)
    project = tmp_path / "parent" / "agent"
    skills_root = project / "agy-worker" / "skills"
    skills_root.mkdir(parents=True)
    (project / "agy-worker" / "repository-skills").mkdir()
    (project.parent / "content" / "skills").mkdir(parents=True)
    (project.parent / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(agy_authoring, "_PROJECT_ROOT", project)

    skills, workspaces = agy_authoring._runtime_paths()

    assert skills == [str(skills_root)]
    assert workspaces == [str(project)]


def test_runtime_paths_preserve_explicit_custom_paths(monkeypatch, tmp_path):
    project = tmp_path / "agent"
    skills_root = project / "agy-worker" / "skills"
    skills_root.mkdir(parents=True)
    custom_skills = tmp_path / "custom-skills"
    custom_skills.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(agy_authoring, "_PROJECT_ROOT", project)
    monkeypatch.setenv(
        "SLIDEGEN_AGY_SKILLS_PATHS",
        os.pathsep.join([str(custom_skills), str(skills_root), str(tmp_path / "missing")]),
    )
    monkeypatch.setenv(
        "SLIDEGEN_AGY_WORKSPACES", os.pathsep.join([str(workspace), str(project)])
    )

    skills, workspaces = agy_authoring._runtime_paths()

    assert skills == [str(custom_skills), str(skills_root)]
    assert workspaces == [str(workspace), str(project)]


def _fake_popen(stdout: str, returncode: int):
    """Popen-shaped fake: capture()s the request, returns canned stdout."""

    class _Fake:
        def __init__(self):
            self.returncode = None
            self.stdin = io.StringIO()
            self.stderr = io.StringIO()
            self._stdout = stdout
            self._code = returncode
            self.captured = None

        def communicate(self, input=None, timeout=None):
            self.captured = input
            self.returncode = self._code
            return self._stdout, ""

        def kill(self):
            return None

    return _Fake()


def test_generate_sends_skills_workspaces_and_returns_text(monkeypatch):
    fake = _fake_popen('{"ok": true, "text": "<!doctype html>"}\n', 0)

    monkeypatch.setattr(
        agy_authoring, "_runtime_paths", lambda: (["/skills"], ["/work"])
    )
    monkeypatch.setattr(agy_authoring.subprocess, "Popen", lambda *a, **k: fake)

    result = agy_authoring.generate(
        contents="make a slide",
        system="system",
        model="gemini-test",
    )

    assert result == "<!doctype html>"
    assert json.loads(fake.captured) == {
        "contents": "make a slide",
        "contents_files": [],
        "triggers": [],
        "subagents": [],
        "system": "system",
        "model": "gemini-test",
        "skills_paths": ["/skills"],
        "workspaces": ["/work"],
    }


def test_generate_prepends_explicit_workspace(monkeypatch, tmp_path):
    fake = _fake_popen('{"ok": true, "text": "done"}\n', 0)
    monkeypatch.setattr(
        agy_authoring, "_runtime_paths", lambda: (["/skills"], ["/work"])
    )
    monkeypatch.setattr(agy_authoring.subprocess, "Popen", lambda *a, **k: fake)

    result = agy_authoring.generate(
        contents="brief",
        system="system",
        model="model",
        workspaces=[str(tmp_path)],
    )

    assert result == "done"
    assert json.loads(fake.captured)["workspaces"] == [
        str(tmp_path.resolve()),
        "/work",
    ]


def test_generate_forwards_live_events(monkeypatch):
    fake = _fake_popen('{"ok": true, "text": "[]"}\n', 0)
    monkeypatch.setattr(agy_authoring, "_runtime_paths", lambda: ([], []))
    monkeypatch.setattr(agy_authoring.subprocess, "Popen", lambda *a, **k: fake)

    seen = []
    agy_authoring._pump_stderr(
        io.StringIO('noise\n{"agy_evt": "tool", "name": "run_command"}\n'),
        seen.append,
    )
    import time

    deadline = time.time() + 2
    while not seen and time.time() < deadline:
        time.sleep(0.01)
    assert seen == [{"agy_evt": "tool", "name": "run_command"}]


def test_generate_surfaces_worker_failure(monkeypatch):
    fake = _fake_popen('{"ok": false, "error": "no credentials"}\n', 1)
    monkeypatch.setattr(agy_authoring, "_runtime_paths", lambda: ([], []))
    monkeypatch.setattr(agy_authoring.subprocess, "Popen", lambda *a, **k: fake)

    with pytest.raises(RuntimeError, match="no credentials"):
        agy_authoring.generate(contents="x", system="y", model="z")
