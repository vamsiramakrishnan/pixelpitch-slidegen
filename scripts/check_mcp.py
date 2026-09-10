"""Repeatable local MCP checks. Never deploys or writes to Google Cloud."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from prepare_agy_skills import REQUIRED_SKILLS

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".tmp" / "mcp-checks"


def run(*args, env=None, cwd=ROOT):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def cloud():
    """Use a fresh loopback emulator, with anonymous clients and random projects."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    host = f"127.0.0.1:{port}"
    env = {**os.environ, "FIRESTORE_EMULATOR_HOST": host}
    with (EVIDENCE / "firestore.log").open("w") as log:
        process = subprocess.Popen(
            [
                "gcloud",
                "emulators",
                "firestore",
                "start",
                f"--host-port={host}",
                "--quiet",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Firestore emulator did not start. See {EVIDENCE / 'firestore.log'}"
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.2)
            run(
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_mcp_firestore.py",
                "tests/integration/test_mcp_cloud.py",
                "tests/unit/test_mcp_auth.py",
                "-q",
                env=env,
            )
        finally:
            # Only the process group created by this check is signalled.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def context():
    """Inspect the exact Cloud SDK upload matcher without submitting a build."""
    sdk = subprocess.check_output(
        ["gcloud", "info", "--format=value(installation.sdk_root)"], text=True
    ).strip()
    probe = """
import json
from googlecloudsdk.command_lib.util import gcloudignore
chooser = gcloudignore.GetFileChooserForDir('.', write_on_disk=False,
    ignore_file='mcp-app/Dockerfile.dockerignore')
print(json.dumps(sorted(chooser.GetIncludedFiles('.', include_dirs=False))))
"""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([f"{sdk}/lib", f"{sdk}/lib/third_party"]),
    }
    files = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", probe], cwd=ROOT, env=env, text=True
        )
    )
    required = {
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "licenses").glob("*.txt")),
        "app/mcp_cloud.py",
        "app/mcp_firestore.py",
        "app/mcp_auth.py",
        "agy-worker/guards.py",
        "mcp-app/src/app.ts",
        "mcp-app/package-lock.json",
        "mcp-app/Dockerfile",
        *(f"agy-worker/skills/{name}/SKILL.md" for name in REQUIRED_SKILLS),
    }
    if missing := required - set(files):
        raise RuntimeError(f"Missing build inputs: {sorted(missing)}")
    if any(name.startswith("agy-worker/repository-skills/") for name in files):
        raise RuntimeError("The unrelated repository skill catalog must not be uploaded")
    skill_manifests = {
        name
        for name in files
        if name.startswith("agy-worker/skills/") and name.endswith("/SKILL.md")
    }
    expected_skills = {
        f"agy-worker/skills/{name}/SKILL.md" for name in REQUIRED_SKILLS
    }
    if skill_manifests != expected_skills:
        raise RuntimeError("The authoring image must contain exactly four slide skills")
    for name in files:
        path = Path(name)
        if any(
            part.startswith(".env")
            or part in {".adk", ".tmp", ".venv", "node_modules", "__pycache__"}
            for part in path.parts
        ) or path.suffix in {".db", ".sqlite", ".pem", ".key"}:
            raise RuntimeError(f"Unsafe build input: {name}")
        if path.parts[0] not in {
            "app",
            "agy-worker",
            "mcp-app",
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "licenses",
        }:
            raise RuntimeError(f"Unexpected build input: {name}")
    (EVIDENCE / "upload-files.json").write_text(json.dumps(files, indent=2) + "\n")
    print(
        f"Verified {len(files)} upload files. Manifest: {EVIDENCE / 'upload-files.json'}"
    )


def local():
    run(sys.executable, "-m", "pytest", "tests/unit", "tests/integration", "-q")
    run("npm", "run", "build", cwd=ROOT / "mcp-app")
    run("npm", "test", cwd=ROOT / "mcp-app")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("local", "cloud", "context", "all"))
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for name, check in (("context", context), ("cloud", cloud), ("local", local)):
        if args.check in (name, "all"):
            check()


if __name__ == "__main__":
    main()
