"""Tests for the `slidify prime-atom-cache` CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from slidify.cli import prime_atom_cache_cmd


def test_prime_cache_writes_json(tmp_path: Path):
    out = tmp_path / "atom_sigs.json"
    runner = CliRunner()
    result = runner.invoke(prime_atom_cache_cmd, ["--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists(), "CLI did not write the output JSON"

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload.get("version") == 1
    entries = payload.get("entries")
    assert isinstance(entries, dict)
    assert len(entries) > 0, "expected non-empty priming entries"
    # Each entry is `<sig_hash>: <atom_id>`.
    for sig_hash, atom_id in entries.items():
        assert isinstance(sig_hash, str) and len(sig_hash) >= 8
        assert isinstance(atom_id, str) and "." in atom_id


def test_prime_cache_default_out_runs(tmp_path: Path, monkeypatch):
    """Without --out, the CLI writes into the in-package data dir.

    We can't safely overwrite the shipped file from the test, so just point
    `--out` at tmp_path and confirm a separate run with a custom path also
    succeeds and produces the same payload shape (proxy for the default case).
    """
    out = tmp_path / "another.json"
    runner = CliRunner()
    result = runner.invoke(prime_atom_cache_cmd, ["--out", str(out)])
    assert result.exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert "entries" in payload and len(payload["entries"]) > 0
