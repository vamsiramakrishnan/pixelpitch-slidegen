"""Keep the repository license, notices, and first-party metadata consistent."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_apache_license_text_is_preserved():
    # Official https://www.apache.org/licenses/LICENSE-2.0.txt, checked
    # 2026-09-10, without its initial blank line. Normalize CRLF for Windows.
    text = (ROOT / "LICENSE").read_text().lstrip("\n")
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    )


@pytest.mark.parametrize(
    "relative_path",
    ["pyproject.toml", "agy-worker/pyproject.toml", "renderer/pyproject.toml"],
)
def test_python_packages_declare_apache_license(relative_path):
    project = tomllib.loads((ROOT / relative_path).read_text())["project"]
    assert project["license"] == "Apache-2.0"


def test_widget_license_matches_lockfile():
    package = json.loads((ROOT / "mcp-app/package.json").read_text())
    locked = json.loads((ROOT / "mcp-app/package-lock.json").read_text())
    assert package["license"] == locked["packages"][""]["license"] == "Apache-2.0"


def test_root_package_includes_readme_and_license_notices():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["readme"] == "README.md"
    included = {
        path.relative_to(ROOT).as_posix()
        for pattern in project["license-files"]
        for path in ROOT.glob(pattern)
    }
    expected = {
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        *(path.relative_to(ROOT).as_posix() for path in (ROOT / "licenses").glob("*.txt")),
    }
    assert included >= expected


@pytest.mark.parametrize(
    "relative_path",
    ["Dockerfile", "mcp-app/Dockerfile", "renderer/Dockerfile", "template-worker/Dockerfile"],
)
def test_runtime_images_copy_license_notices(relative_path):
    dockerfile = (ROOT / relative_path).read_text()
    assert "COPY LICENSE THIRD_PARTY_NOTICES.md ./" in dockerfile
    assert "COPY licenses/ ./licenses/" in dockerfile
