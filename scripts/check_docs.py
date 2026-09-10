"""Check public operator docs without running their commands or contacting cloud APIs."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FENCES = re.compile(r"^ *```([^\n]*)\n(.*?)^ *``` *$", re.M | re.S)
LINKS = re.compile(r"!?\[[^\]\n]*\]\(([^\s)]+)\)")
PRIVATE_VALUES = {
    "literal cloud project number": re.compile(r"\b\d{10,15}\b"),
    "literal Cloud Run hostname": re.compile(r"https://[a-z0-9.-]+\.run\.app\b"),
    "literal service account project": re.compile(
        r"@(?!gcp-sa-)[a-z0-9-]+\.iam\.gserviceaccount\.com"
    ),
    "local home path": re.compile(r"/home/[^/\s]+/"),
    "credential material": re.compile(
        r"GOCSPX-[\w-]+|AIza[\w-]{35}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"
    ),
}


def documents(root: Path) -> list[Path]:
    return sorted(
        {
            *(
                root / name
                for name in ("README.md", "CLOUD_SHELL.md", "TUTORIAL.md", "AGENTS.md")
            ),
            *root.joinpath("docs").rglob("*.md"),
            *root.joinpath("mcp-app").glob("*.md"),
            *root.joinpath("renderer").glob("*.md"),
        }
    )


def public_configurations(root: Path) -> list[Path]:
    return [root / name for name in (".env.example", "deploy.env.example", "mise.toml")]


def check_private_values(text: str) -> list[str]:
    return [
        f"replace {label} with configuration or an uppercase placeholder"
        for label, pattern in PRIVATE_VALUES.items()
        if pattern.search(text)
    ]


def headings(text: str) -> list[tuple[int, str]]:
    return [
        (len(level), label.rstrip(" #"))
        for level, label in re.findall(r"^(#{1,6}) +(.+)$", FENCES.sub("", text), re.M)
    ]


def anchors(text: str) -> set[str]:
    counts: dict[str, int] = {}
    result = set()
    for _, label in headings(text):
        slug = re.sub(r"[^\w\- ]", "", label.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def check_document(path: Path, text: str, tasks: set[str], root: Path) -> list[str]:
    errors = []
    levels = [level for level, _ in headings(text)]
    if not levels or levels[0] != 1 or levels.count(1) != 1:
        errors.append("use exactly one H1, before other headings")
    if any(after > before + 1 for before, after in pairwise(levels)):
        errors.append("heading levels must not skip")
    errors.extend(check_private_values(text))
    for task in sorted(set(re.findall(r"\bmise run ([a-z][\w-]*)", text))):
        if task not in tasks:
            errors.append(f"unknown mise task: {task}")
    for destination in LINKS.findall(FENCES.sub("", text)):
        url = urlsplit(destination.strip("<>"))
        if url.scheme or url.netloc:
            continue
        target = (path.parent / unquote(url.path)).resolve() if url.path else path
        if not target.is_relative_to(root):
            errors.append(f"link escapes agent documentation: {destination}")
        elif not target.exists():
            errors.append(f"missing link target: {destination}")
        elif (
            url.fragment
            and target.suffix == ".md"
            and unquote(url.fragment) not in anchors(target.read_text())
        ):
            errors.append(f"missing heading anchor: {destination}")
    for language, snippet in FENCES.findall(text):
        if language.strip() not in {"bash", "sh", "shell"}:
            continue
        result = subprocess.run(
            ["bash", "-n"], input=snippet, text=True, capture_output=True
        )
        if result.returncode:
            errors.append(f"invalid shell syntax: {result.stderr.strip()}")
    return errors


def check_svg(path: Path) -> list[str]:
    try:
        svg = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"invalid SVG XML: {exc}"]
    ns = "{http://www.w3.org/2000/svg}"
    errors = []
    if svg.tag != ns + "svg" or not svg.get("viewBox") or svg.get("role") != "img":
        errors.append("SVG needs its namespace, viewBox, and image role")
    ids = {element.get("id"): element for element in svg.iter() if element.get("id")}
    labels = svg.get("aria-labelledby", "").split()
    if len(labels) < 2 or any(label not in ids for label in labels):
        errors.append("SVG must reference its title and description")
    for tag in ("title", "desc"):
        element = svg.find(ns + tag)
        if element is None or not (element.text or "").strip():
            errors.append(f"SVG needs a readable {tag}")
    for element in svg.iter():
        if element.tag in {ns + "script", ns + "foreignObject", ns + "image"}:
            errors.append(
                "architecture SVG must be self-contained, with no scripts or embedded images"
            )
        if any(
            key.rsplit("}", 1)[-1] == "href" and not value.startswith("#")
            for key, value in element.attrib.items()
        ):
            errors.append("architecture SVG must not load external resources")
    return errors


def main() -> int:
    tasks = set(tomllib.loads((ROOT / "mise.toml").read_text())["tasks"])
    paths = documents(ROOT)
    errors = []
    for path in paths:
        for issue in check_document(path, path.read_text(), tasks, ROOT):
            errors.append(f"{path.relative_to(ROOT)}: {issue}")
    configurations = public_configurations(ROOT)
    for path in configurations:
        errors.extend(
            f"{path.relative_to(ROOT)}: {issue}"
            for issue in check_private_values(path.read_text())
        )
    diagrams = sorted((ROOT / "docs/assets").glob("*.svg"))
    if not diagrams:
        errors.append("docs/assets: architecture SVG is missing")
    for path in diagrams:
        errors.extend(f"{path.relative_to(ROOT)}: {issue}" for issue in check_svg(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Documentation checks passed: {len(paths)} pages, {len(configurations)} configuration files, "
        f"{len(diagrams)} SVG files. No cloud commands executed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
