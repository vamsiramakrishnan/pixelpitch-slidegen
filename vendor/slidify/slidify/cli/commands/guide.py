"""Guide command for shipped long-form documentation."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click


def _guides_dir() -> Path:
    return Path(__file__).parents[1] / "guides"


def _list_guides() -> list[tuple[str, str]]:
    """Return ``[(topic, one_line_summary), ...]`` from each guide."""
    items: list[tuple[str, str]] = []
    d = _guides_dir()
    if not d.exists():
        return items
    for path in sorted(d.glob("*.md")):
        topic = path.stem
        text = path.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines()]
        summary = ""
        in_code = False
        for i, ln in enumerate(lines):
            if ln.startswith("# "):
                for ln2 in lines[i + 1:]:
                    if ln2.startswith("```"):
                        in_code = not in_code
                        continue
                    if in_code:
                        continue
                    if not ln2 or ln2.startswith(("#", "|", "-", "*", "1.", "2.")):
                        continue
                    summary = ln2
                    break
                break
        items.append((topic, summary))
    return items


def _extract_section(md: str, header: str) -> str:
    """Return the first H2 body whose title contains ``header``."""
    target = header.lower()
    out: list[str] = []
    in_section = False
    for line in md.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## "):
            title = stripped[3:].strip().lower()
            if in_section:
                break
            if target in title:
                in_section = True
                out.append(line)
                continue
        elif stripped.startswith("# ") and in_section:
            break
        if in_section:
            out.append(line)
    return "\n".join(out).rstrip()


def _toc(md: str) -> str:
    """Markdown table of contents using H1 and H2 headings."""
    lines: list[str] = []
    for ln in md.splitlines():
        s = ln.lstrip()
        if s.startswith("# "):
            lines.append(s[2:].strip())
        elif s.startswith("## "):
            lines.append("  " + s[3:].strip())
    return "\n".join(lines)


def _grep(md: str, pattern: str, context: int = 1) -> str:
    """Return matching lines with context lines before and after each hit."""
    rx = re.compile(pattern, re.IGNORECASE)
    lines = md.splitlines()
    keep = [False] * len(lines)
    for i, ln in enumerate(lines):
        if rx.search(ln):
            for j in range(max(0, i - context), min(len(lines), i + context + 1)):
                keep[j] = True
    out: list[str] = []
    last_kept = -2
    for i, k in enumerate(keep):
        if k:
            if last_kept >= 0 and i - last_kept > 1:
                out.append("--")
            out.append(f"{i + 1}: {lines[i]}")
            last_kept = i
    return "\n".join(out)


@click.command(name="guide")
@click.argument("topic", required=False)
@click.option(
    "--section",
    default=None,
    help="Extract only this H2 section (case-insensitive substring).",
)
@click.option("--toc", "show_toc", is_flag=True, help="Print the table of contents only.")
@click.option(
    "--grep",
    "grep_pattern",
    default=None,
    help="Show lines matching this regex (with surrounding context).",
)
@click.option(
    "--search",
    default=None,
    help="Search ALL guides for the regex; print matching topics + lines.",
)
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
def guide_cmd(
    topic: str | None,
    section: str | None,
    show_toc: bool,
    grep_pattern: str | None,
    search: str | None,
    json_out: bool,
) -> None:
    """Read shipped guides with built-in section and grep helpers."""
    if search:
        rx = re.compile(search, re.IGNORECASE)
        hits: list[dict] = []
        for t, _ in _list_guides():
            text = (_guides_dir() / f"{t}.md").read_text(encoding="utf-8")
            for i, ln in enumerate(text.splitlines(), 1):
                if rx.search(ln):
                    hits.append({"topic": t, "line": i, "text": ln.rstrip()})
        if json_out:
            click.echo(json.dumps({"query": search, "hits": hits}, indent=2))
        else:
            if not hits:
                click.echo(f"no matches for /{search}/")
                return
            for h in hits:
                click.echo(f"{h['topic']:<22} {h['line']:>4}: {h['text']}")
        return

    if not topic:
        items = _list_guides()
        if json_out:
            click.echo(
                json.dumps(
                    {"guides": [{"topic": t, "summary": s} for t, s in items]},
                    indent=2,
                )
            )
            return
        if not items:
            click.echo("no guides shipped")
            return
        click.echo("Available guides:")
        for t, s in items:
            click.echo(f"  {click.style(t, fg='cyan'):<32} {click.style(s, dim=True)}")
        click.echo()
        click.echo("Read one with:  slidify guide <topic>")
        click.echo("Drill in with:  slidify guide <topic> --section ...  --grep ...  --toc")
        return

    path = _guides_dir() / f"{topic}.md"
    if not path.exists():
        known = [t for t, _ in _list_guides()]
        if json_out:
            click.echo(
                json.dumps({"error": f"unknown guide: {topic}", "known": known}, indent=2)
            )
        else:
            click.echo(f"unknown guide: {topic}", err=True)
            click.echo(f"available: {', '.join(known)}", err=True)
        sys.exit(2)
    md = path.read_text(encoding="utf-8")

    if show_toc:
        click.echo(_toc(md))
        return
    if section:
        body = _extract_section(md, section)
        if not body:
            click.echo(f"no section matching '{section}' in {topic}", err=True)
            sys.exit(2)
        click.echo(body)
        return
    if grep_pattern:
        result = _grep(md, grep_pattern)
        if not result:
            click.echo(f"no matches for /{grep_pattern}/ in {topic}", err=True)
            sys.exit(1)
        click.echo(result)
        return

    click.echo(md)

