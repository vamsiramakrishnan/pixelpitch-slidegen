"""The local lifecycle must preserve the independent queue across API restarts."""

import argparse
from types import SimpleNamespace

from scripts import dev_local


def test_up_preserves_saved_jobs_and_reuses_the_existing_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_local, "STATE_ROOT", tmp_path)
    where = dev_local.state_dir(18099)
    (where / "a2a").mkdir(parents=True)
    saved = where / "a2a" / "jobs.sqlite"
    saved.write_bytes(b"preserved queue")
    monkeypatch.setattr(
        dev_local,
        "read_pid",
        lambda port, kind="server": 4242 if kind == "worker" else None,
    )
    started = []

    def spawn(command, **kwargs):
        started.append(command)
        assert kwargs["env"]["SLIDEGEN_A2A_JOB_DB"] == str(saved)
        return SimpleNamespace(pid=4243)

    monkeypatch.setattr(dev_local.subprocess, "Popen", spawn)
    monkeypatch.setattr(dev_local, "wait_for", lambda *args: True)
    monkeypatch.setattr(dev_local, "print_access", lambda *args: None)
    args = argparse.Namespace(
        port=18099,
        public_base=None,
        sync=False,
        host="127.0.0.1",
        boot_timeout=5,
        no_check=True,
    )
    assert dev_local.up(args) == 0
    assert saved.read_bytes() == b"preserved queue"
    assert len(started) == 1 and "uvicorn" in started[0]


def test_api_only_restart_does_not_signal_the_worker(monkeypatch):
    stopped = []
    monkeypatch.setattr(
        dev_local, "stop_process", lambda port, kind: stopped.append((port, kind))
    )
    dev_local.down(argparse.Namespace(port=18090, keep_worker=True))
    assert stopped == [(18090, "server")]


def test_reused_pid_is_not_treated_as_our_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_local, "STATE_ROOT", tmp_path)
    where = dev_local.state_dir(18090)
    where.mkdir()
    (where / "worker.pid").write_text("123")
    monkeypatch.setattr(dev_local.os, "kill", lambda *args: None)
    monkeypatch.setattr(
        dev_local.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="python unrelated.py"),
    )
    assert dev_local.read_pid(18090, "worker") is None
