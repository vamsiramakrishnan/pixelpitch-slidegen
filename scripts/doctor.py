"""Check local development prerequisites without credentials or cloud requests."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from prepare_agy_skills import prepare

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def python_check(root: Path, name: str, relative: str, code: str, fix: str) -> Check:
    executable = root / relative
    if not executable.is_file():
        return Check(name, False, "Environment is missing", fix)
    try:
        result = subprocess.run(
            [str(executable), "-c", code],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Check(name, False, "Environment could not complete its check", fix)
    return Check(
        name,
        result.returncode == 0,
        "Available" if result.returncode == 0 else "Dependency check failed",
        fix,
    )


def checks(root: Path = ROOT) -> list[Check]:
    results = [
        Check(
            tool,
            shutil.which(tool) is not None,
            "Found on PATH" if shutil.which(tool) else "Not installed",
            "mise install",
        )
        for tool in ("uv", "node", "npm")
    ]
    try:
        summary = prepare(check=True)
        results.append(
            Check("Slide skills", True, f"{summary['skills']} focused skills")
        )
    except (OSError, RuntimeError) as exc:
        results.append(
            Check("Slide skills", False, str(exc), "Restore agy-worker/skills from Git")
        )
    results.extend(
        [
            python_check(
                root,
                "App environment",
                ".venv/bin/python",
                "import importlib.util; assert all(importlib.util.find_spec(n) for n in ('google.adk', 'mcp', 'playwright', 'PIL'))",
                "mise run setup-dev",
            ),
            python_check(
                root,
                "Isolated authoring",
                "agy-worker/.venv/bin/python",
                "import importlib.util; assert importlib.util.find_spec('google.antigravity')",
                "mise run setup",
            ),
            python_check(
                root,
                "Chromium binary",
                ".venv/bin/python",
                "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); found=Path(p.chromium.executable_path).is_file(); p.stop(); assert found",
                "uv run --extra mcp-app playwright install chromium",
            ),
        ]
    )
    for name in ("index.html", "host/host.html"):
        path = root / "mcp-app/dist" / name
        results.append(
            Check(
                f"Widget {name}",
                path.is_file(),
                "Built" if path.is_file() else "Build is missing",
                "mise run build-ui",
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = checks()
    ok = all(check.ok for check in results)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "scope": "local-only",
                    "checks": [asdict(check) for check in results],
                },
                indent=2,
            )
        )
    else:
        for check in results:
            print(f"{'PASS' if check.ok else 'FIX '}  {check.name}: {check.detail}")
            if not check.ok and check.fix:
                print(f"      {check.fix}")
        print(
            "\nNext: mise run demo"
            if ok
            else "\nFix the items above, then rerun mise run doctor."
        )
        print(
            "Cloud access, model availability, and Gemini Enterprise rendering are not checked."
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
