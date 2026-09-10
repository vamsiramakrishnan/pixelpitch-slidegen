"""Harvest command and promotion queue helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from slidify.models import UnmatchedSignature


def _harvest_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.glob("**/*.html") if p.is_file())


async def _harvest_one(html_path: Path) -> list[UnmatchedSignature]:
    from tempfile import NamedTemporaryFile

    from slidify.api import ConversionConfig, convert

    cfg = ConversionConfig(run_oracle=False, run_tier3=False, keep_plans_for_oracle=False)
    with NamedTemporaryFile(suffix=".pptx", delete=False) as tf:
        out = Path(tf.name)
    try:
        result = await convert(html_path, out, cfg)
        return list(result.unmatched_signatures)
    finally:
        try:
            out.unlink()
        except FileNotFoundError:
            pass


_PROMOTE_YAML_HEADER = (
    "# AUTO-GENERATED queue of harvested pattern stubs.\n"
    "#\n"
    "# Each entry was written by `slidify harvest --promote-yaml` from an\n"
    "# unmatched DOM signature observed in a real deck. Stubs default to\n"
    "# `kind: Raster` so a stub never silently regresses fidelity. A human\n"
    "# (or LLM) reviewer should:\n"
    "#   1. Tighten the `match` clauses (the auto-inferred `anchor.tag_in`\n"
    "#      is the lowest-resolution predicate that fits the signature).\n"
    "#   2. Promote `kind` from `Raster` to `NativeShape` / `NativeText` /\n"
    "#      `NativeSvg` once the recipe is correct.\n"
    "#   3. Move the entry into `slidify/patterns/data/patterns.yaml` (or\n"
    "#      `atoms.yaml` for atom-keyed recipes) and delete it from here.\n"
    "#\n"
    "# Re-running `slidify harvest --promote-yaml` is idempotent: existing\n"
    "# `id:` values are preserved.\n"
)


def _infer_tag_from_sig(sig: str) -> str:
    """Return the signature's leading tag as an uppercase matcher tag."""
    if not sig:
        return "DIV"
    head = sig.split("(", 1)[0].strip()
    return head.upper() or "DIV"


def _build_promotion_stubs(
    sigs: list[UnmatchedSignature], min_count: int
) -> list[dict]:
    """Build YAML-serialisable stub dicts from unmatched signatures."""
    stubs: list[dict] = []
    for sig in sigs:
        if sig.n_occurrences < min_count:
            continue
        tag = _infer_tag_from_sig(sig.sig)
        stubs.append({
            "id": f"harvested-{sig.sig_hash[:8]}",
            "priority": 999,
            "match": {
                "anchor.tag_in": [tag],
            },
            "_meta": {
                "originating_signature": sig.sig,
                "occurrences": sig.n_occurrences,
                "sample_classes": sig.sample_classes,
            },
            "emit": {
                "kind": "Raster",
                "confidence": 0.5,
                "metadata": {
                    "recipe": f"harvested_{sig.sig_hash[:8]}",
                    "source": "harvester",
                },
            },
        })
    return stubs


def promote_unmatched_to_yaml(
    sigs: list[UnmatchedSignature],
    out_path: Path,
    *,
    min_count: int = 3,
) -> int:
    """Append harvested stubs to ``out_path`` and return the new entry count."""
    import yaml

    new_stubs = _build_promotion_stubs(sigs, min_count)
    if not new_stubs:
        return 0

    existing: dict = {}
    if out_path.exists():
        try:
            existing = yaml.safe_load(out_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            existing = {}

    existing_patterns = list(existing.get("patterns", []) or [])
    seen_ids = {p.get("id") for p in existing_patterns if isinstance(p, dict)}

    n_new = 0
    for stub in new_stubs:
        if stub["id"] in seen_ids:
            continue
        meta = stub.pop("_meta", {})
        existing_patterns.append(stub)
        seen_ids.add(stub["id"])
        stub["_meta"] = meta
        n_new += 1

    payload = {"patterns": existing_patterns}

    lines: list[str] = [_PROMOTE_YAML_HEADER, "patterns:\n"]
    for entry in payload["patterns"]:
        meta = entry.pop("_meta", None) if isinstance(entry, dict) else None
        if isinstance(meta, dict) and meta:
            sig_line = (meta.get("originating_signature") or "")[:120]
            occ = meta.get("occurrences", "?")
            cls = (meta.get("sample_classes") or "")[:80]
            lines.append("\n")
            lines.append(f"  # Originating signature: {sig_line}\n")
            lines.append(f"  # Occurrences in corpus: {occ}\n")
            if cls:
                lines.append(f"  # Sample classes: {cls}\n")
            lines.append(
                "  # AUTO-GENERATED - review before promoting from Raster default.\n"
            )
        block = yaml.safe_dump(
            [entry], sort_keys=False, default_flow_style=False, indent=2
        )
        lines.append(block)

    out_path.write_text("".join(lines), encoding="utf-8")
    return n_new


@click.command(name="harvest")
@click.argument(
    "input_path",
    type=click.Path(exists=True, dir_okay=True, file_okay=True, path_type=Path),
)
@click.option(
    "--output", "-o", "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write clusters.json (CONTRACT-v2 §G.3 schema) to this path.",
)
@click.option(
    "--top-n",
    type=int,
    default=50,
    show_default=True,
    help="Cap the number of clusters written to clusters.json.",
)
@click.option(
    "--min-occurrences",
    type=int,
    default=1,
    show_default=True,
    help="Drop clusters whose total instance count is below this threshold.",
)
@click.option(
    "--out-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    hidden=True,
)
@click.option(
    "--top",
    type=int,
    default=None,
    hidden=True,
)
@click.option(
    "--promote-yaml",
    "promote_yaml",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Append a YAML stub for each unmatched signature with at least "
        "--min-count occurrences. The output file becomes a deterministic "
        "review queue: a human / LLM tightens the `match` clauses and "
        "promotes `kind: Raster` to a native emit kind."
    ),
)
@click.option(
    "--min-count",
    "min_count",
    type=int,
    default=3,
    show_default=True,
    help="Minimum occurrences before --promote-yaml writes a stub.",
)
@click.option(
    "--json", "json_out",
    is_flag=True,
    default=False,
    help=(
        "Print the clusters.json payload to stdout instead of (or in "
        "addition to) writing it to --output. Implies machine-readable "
        "output for piping into other tools."
    ),
)
@click.option(
    "--progress",
    type=click.Choice(["plain", "jsonl", "both", "off"], case_sensitive=False),
    default="plain",
    show_default=True,
    help="Emit LLM-readable progress events to stderr.",
)
@click.option(
    "--progress-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write progress events as JSONL to this file.",
)
def harvest(
    input_path: Path,
    output_path: Path | None,
    top_n: int,
    min_occurrences: int,
    out_json: Path | None,
    top: int | None,
    promote_yaml: Path | None,
    min_count: int,
    json_out: bool,
    progress: str,
    progress_file: Path | None,
) -> None:
    """Run slidify across a corpus and emit clustered Tier-0 candidate atoms."""
    from slidify.harvester import aggregate_corpus, report_to_dict, write_report
    from slidify.progress import ProgressReporter, progress_callback

    def _info(msg: str = "") -> None:
        click.echo(msg, err=json_out)

    if out_json is not None and output_path is None:
        output_path = out_json
        click.echo(
            click.style(
                "warning: --out-json is deprecated; use --output / -o.",
                fg="yellow",
            ),
            err=True,
        )
    if top is not None:
        top_n = top
        click.echo(
            click.style(
                "warning: --top is deprecated; use --top-n.",
                fg="yellow",
            ),
            err=True,
        )

    if not input_path.exists():
        click.echo(f"slidify harvest: input path does not exist: {input_path}", err=True)
        sys.exit(2)

    progress_mode = progress.lower()
    if json_out and progress_mode == "plain":
        progress_mode = "off"
    reporter = ProgressReporter(
        mode=progress_mode,
        stream=sys.stderr,
        jsonl_path=progress_file,
    )

    _info(f"Harvesting {input_path}...")
    reporter.emit(
        {
            "event": "harvest.start",
            "stage": "setup",
            "message": f"harvesting {input_path}",
            "path": str(input_path),
        }
    )

    def _on_progress(path: Path, n_sigs: int) -> None:
        _info(f"  {path.name:<48} {n_sigs:>4d} unmatched")

    report = aggregate_corpus(
        input_path,
        min_occurrences=min_occurrences,
        on_progress=_on_progress,
        progress=progress_callback(reporter),
    )

    if report.decks_processed == 0:
        click.echo(f"slidify harvest: no decks processed under {input_path}", err=True)
        sys.exit(1)

    _info("")
    _info(f"Decks processed       : {report.decks_processed}")
    _info(f"Total unmatched units : {report.total_unmatched}")
    _info(f"Unique signatures     : {report.unique_signatures}")
    if report.errors:
        _info(
            click.style(f"Per-deck errors       : {len(report.errors)}", fg="yellow")
        )

    n_show = min(top_n, len(report.clusters))
    if n_show:
        _info("")
        _info(f"Top {n_show} clusters:")
        _info("-" * 80)
        for c in report.clusters[:n_show]:
            cand = report.candidates.get(c.id)
            atom = cand.candidate_atom_id if cand else "?"
            axis = cand.candidate_axis if cand else "?"
            _info(
                f"  {c.id}  x{c.instances:<4d}  "
                f"{int(c.bbox_typical.get('w_avg', 0)):>4d}x"
                f"{int(c.bbox_typical.get('h_avg', 0)):<3d}  "
                f"-> {axis}  {atom}"
            )
            if c.sample_classes:
                _info(f"      classes : {', '.join(c.sample_classes[:3])}")

    if output_path is not None:
        write_report(report, output_path, top_n=top_n)
        reporter.emit(
            {
                "event": "harvest.write.done",
                "stage": "write",
                "message": f"wrote clusters JSON to {output_path}",
                "path": str(output_path),
            }
        )
        _info("")
        _info(f"Wrote clusters.json   : {output_path}")
    if json_out:
        click.echo(json.dumps(report_to_dict(report, top_n=top_n), indent=2))
    elif output_path is None:
        _info("")
        _info(
            "(no --output / --json supplied - pass --output PATH or --json "
            "to persist clusters.json)"
        )

    if promote_yaml:
        promote_sigs = [
            UnmatchedSignature(
                sig=c.signature,
                sig_hash=c.sig_hash,
                bbox_w=int(c.bbox_typical.get("w_avg", 0)),
                bbox_h=int(c.bbox_typical.get("h_avg", 0)),
                sample_classes=", ".join(c.sample_classes[:3]),
                sample_text=(c.sample_text[0] if c.sample_text else ""),
                n_occurrences=c.instances,
            )
            for c in report.clusters
        ]
        n_new = promote_unmatched_to_yaml(
            promote_sigs, promote_yaml, min_count=min_count
        )
        click.echo(
            f"\nPromoted {n_new} stub(s) (min_count={min_count}) -> {promote_yaml}"
        )

    reporter.close()

