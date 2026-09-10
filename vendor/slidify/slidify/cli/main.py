"""slidify CLI entrypoints.

Subcommands:

  slidify convert     <input> <output.pptx>     one-shot conversion
  slidify harvest     <dir>                     run a corpus, emit pattern suggestions
  slidify compat                                CSS/HTML compatibility matrix
  slidify capture-gif <html>                    HTML animation → animated GIF
  slidify doctor                                check system dependencies
  slidify version                               print version info

For backward compatibility, calling `slidify <input> <output.pptx>` (without a
subcommand) defaults to `convert`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import click

from slidify import __version__
from slidify.api import ConversionConfig, convert
from slidify.cli.commands.guide import guide_cmd
from slidify.cli.commands.harvest import (
    _build_promotion_stubs,
    harvest,
    promote_unmatched_to_yaml,
)

__all__ = [
    "_build_promotion_stubs",
    "cli",
    "harvest",
    "main",
    "prime_atom_cache_cmd",
    "promote_unmatched_to_yaml",
]

# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


def _convert_options(fn):
    """Shared options between the explicit convert subcommand and the default."""
    fn = click.option(
        "--json", "json_out",
        is_flag=True,
        help="Print the full ConversionResult as JSON to stdout. Implies --quiet.",
    )(fn)
    fn = click.option(
        "--quiet", "-q",
        is_flag=True,
        help="Suppress the human summary; print only the output path.",
    )(fn)
    fn = click.option(
        "--progress",
        type=click.Choice(["plain", "jsonl", "both", "off"], case_sensitive=False),
        default="plain",
        show_default=True,
        help="Emit LLM-readable progress events to stderr.",
    )(fn)
    fn = click.option(
        "--progress-file",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help="Write progress events as JSONL to this file.",
    )(fn)
    fn = click.option("--report-json", type=click.Path(dir_okay=False, path_type=Path), default=None)(fn)
    fn = click.option(
        "--no-differential-render",
        is_flag=True,
        help="Disable the second per-slide screenshot used for surgical-hybrid "
        "background crops. On by default; saves ~150ms/slide at the cost of "
        "occasional text bleed-through into rasterized backgrounds.",
    )(fn)
    fn = click.option(
        "--no-embed-fonts",
        is_flag=True,
        help="Skip embedding source fonts (Inter, etc.) in the .pptx. "
        "On by default; only disable if you trust the destination machine "
        "to have the right fonts installed (it usually doesn't).",
    )(fn)
    fn = click.option(
        "--low-memory",
        is_flag=True,
        help="Drop per-slide state right after emit. Disables oracle auto-correction"
        " but keeps peak memory bounded for huge decks.",
    )(fn)
    fn = click.option("--render-concurrency", type=int, default=4, show_default=True)(fn)
    fn = click.option("--google-location", default=None, help="GCP location/region (for Vertex backends).")(fn)
    fn = click.option("--google-project", default=None, help="GCP project (for Vertex backends).")(fn)
    fn = click.option("--llm-model", default=None, help="Override default model for chosen backend.")(fn)
    fn = click.option(
        "--llm-backend",
        type=click.Choice(
            ["gemini-aistudio", "gemini-vertex", "anthropic", "claude-vertex"],
            case_sensitive=False,
        ),
        default=None,
        help="LLM backend for tier 3. Auto-detected from environment if omitted.",
    )(fn)
    fn = click.option("--no-oracle", is_flag=True, help="Skip the fidelity oracle.")(fn)
    fn = click.option("--no-tier3", is_flag=True, help="Skip the LLM adjudicator.")(fn)
    return fn


def _read_stdin_html() -> str:
    """Read HTML from stdin (used when input path is `-`)."""
    if sys.stdin.isatty():
        click.echo(
            "slidify: input '-' but stdin is a TTY; pipe HTML or pass a path.",
            err=True,
        )
        sys.exit(2)
    return sys.stdin.read()


def _run_convert(
    input_path: Path,
    output_pptx: Path,
    no_tier3: bool,
    no_oracle: bool,
    llm_backend: str | None,
    llm_model: str | None,
    google_project: str | None,
    google_location: str | None,
    render_concurrency: int,
    low_memory: bool,
    no_differential_render: bool,
    no_embed_fonts: bool,
    report_json: Path | None,
    quiet: bool,
    json_out: bool,
    progress: str,
    progress_file: Path | None,
) -> None:
    from slidify.progress import ProgressReporter, progress_callback

    progress_mode = progress.lower()
    if json_out and progress_mode == "plain":
        progress_mode = "off"
    reporter = ProgressReporter(
        mode=progress_mode,
        stream=sys.stderr,
        jsonl_path=progress_file,
    )
    cfg = ConversionConfig(
        run_tier3=not no_tier3,
        run_oracle=not no_oracle,
        llm_backend=llm_backend,
        llm_model=llm_model,
        google_project=google_project,
        google_location=google_location,
        render_concurrency=render_concurrency,
        keep_plans_for_oracle=not low_memory,
        differential_render=not no_differential_render,
        embed_fonts=not no_embed_fonts,
        progress_callback=progress_callback(reporter),
    )

    # Stdin input: `slidify convert - out.pptx` reads the deck from stdin.
    source: Path | str
    if str(input_path) == "-":
        source = _read_stdin_html()
    else:
        if not input_path.exists():
            msg = f"input path does not exist: {input_path}"
            if json_out:
                click.echo(json.dumps({"error": msg, "type": "FileNotFoundError"}, indent=2))
            else:
                click.echo(click.style(f"slidify: {msg}", fg="red"), err=True)
            sys.exit(2)
        source = input_path

    try:
        result = asyncio.run(convert(source, output_pptx, cfg))
    except Exception as e:
        remediation = _error_remediation(e)
        if json_out:
            click.echo(json.dumps({
                "error": str(e),
                "type": type(e).__name__,
                "stage": "convert",
                "_remediation": remediation,
            }, indent=2))
        else:
            click.echo(click.style(f"slidify: conversion failed: {e}", fg="red"), err=True)
            for r in remediation:
                click.echo(click.style("  → ", dim=True) + r, err=True)
        reporter.close()
        sys.exit(2)

    if json_out:
        # Augment with `_next` hints so an LLM agent gets concrete
        # follow-up commands without re-reading the manifest.
        payload = result.model_dump()
        payload["_next"] = _next_steps(result)
        click.echo(json.dumps(payload, indent=2, default=str))
    elif quiet:
        click.echo(str(result.pptx_path))
    else:
        _print_summary(result)
        _print_next_steps(result)

    if report_json:
        report_json.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        if not quiet and not json_out:
            click.echo(f"Report  : {report_json}")

    # Non-zero exit when the deck silently dropped shapes — useful for CI.
    if not result.editability_passed:
        reporter.close()
        sys.exit(3)
    reporter.close()


def _error_remediation(exc: BaseException) -> list[str]:
    """Map exception kinds to actionable next-step commands."""
    name = type(exc).__name__
    msg = str(exc)
    out: list[str] = []
    if name == "FileNotFoundError" or "no such file" in msg.lower():
        out.append("Verify the input path: `ls -la <path>`")
        out.append("Or pipe HTML via stdin: `cat slide.html | slidify convert - out.pptx`")
    if "playwright" in msg.lower() or "chromium" in msg.lower():
        out.append("Run `slidify doctor` to verify Chromium is installed.")
        out.append("Install with: `playwright install chromium --with-deps`")
    if "soffice" in msg.lower() or "libreoffice" in msg.lower():
        out.append("Install LibreOffice: `apt-get install -y libreoffice-impress`")
        out.append("Or skip the oracle: `--no-oracle`")
    if "no slides produced" in msg.lower():
        out.append("Add `<!DOCTYPE html>` between slides, or pass a directory of per-slide files.")
        out.append("Read: `slidify guide authoring --section 'Hard contract'`")
    if not out:
        out.append("Run `slidify doctor` to check the environment.")
        out.append("Read `slidify guide troubleshooting`.")
    return out


def _next_steps(result) -> list[str]:
    """Suggest concrete follow-up commands based on this conversion's state.

    Acts as an instruction-delivery channel: the CLI tells the LLM what to do
    next. Order matters — most-actionable first.
    """
    hints: list[str] = []
    if result.overflow_elements:
        slide_ids = sorted({o.slide_index + 1 for o in result.overflow_elements})
        hints.append(
            f"{len(result.overflow_elements)} overflow element(s) on slide(s) "
            f"{', '.join(map(str, slide_ids))}. "
            "Read each row's `hint` in --report-json (overflow_elements[].hint) "
            "for an atom-aware fix."
        )
    if not result.editability_passed and result.editability_failing_slides:
        hints.append(
            "Editability drift — re-render the failing slides individually:  "
            f"slidify convert <slide.html> <out.pptx>  (slides: "
            f"{', '.join(map(str, result.editability_failing_slides))})"
        )
    failing = [r for r in result.fidelity_reports if not r.passed]
    if failing:
        hints.append(
            f"{len(failing)} slide(s) failed the SSIM/OCR oracle. "
            "Read `slidify guide troubleshooting --section 'native_area_ratio low'`."
        )
    if result.native_area_ratio < 0.6:
        hints.append(
            f"Low native_area_ratio ({result.native_area_ratio:.2f}). "
            "Read `slidify guide authoring --section 'What forces a raster'`."
        )
    if result.unmatched_signatures:
        hints.append(
            f"{len(result.unmatched_signatures)} unmatched DOM signatures recorded. "
            "Run `slidify harvest <corpus_dir> --output clusters.json` "
            "to surface Tier-0 candidates."
        )
    if not hints:
        hints.append("Open the .pptx in PowerPoint to verify editability.")
        hints.append("Run `slidify field <report.json> native_area_ratio` to extract metrics.")
    return hints


def _print_next_steps(result) -> None:
    steps = _next_steps(result)
    if not steps:
        return
    click.echo(click.style("Next:", bold=True))
    for s in steps:
        click.echo(click.style("  → ", dim=True) + s)


def _print_summary(result) -> None:
    """Pretty-print the ConversionResult as a labeled key/value block."""
    G = lambda s: click.style(s, fg="green")  # noqa: E731
    Y = lambda s: click.style(s, fg="yellow")  # noqa: E731
    R = lambda s: click.style(s, fg="red")    # noqa: E731
    D = lambda s: click.style(s, dim=True)    # noqa: E731

    click.echo(D("─" * 64))
    click.echo(f"{'Output':<22}{result.pptx_path}")
    click.echo(f"{'Slides':<22}{result.n_slides}")
    nr = result.native_area_ratio
    nr_color = G if nr >= 0.85 else Y if nr >= 0.6 else R
    click.echo(f"{'Native area ratio':<22}{nr_color(f'{nr:.3f}')}")
    click.echo(f"{'Pattern coverage':<22}{result.pattern_coverage:.3f}")
    if result.pattern_hits:
        top = sorted(result.pattern_hits.items(), key=lambda kv: -kv[1])[:6]
        click.echo(f"{'Top patterns':<22}" + ", ".join(f"{k}×{v}" for k, v in top))
    click.echo(f"{'Cache hit rate':<22}{result.cache_hit_rate:.3f}")
    click.echo(
        f"{'LLM calls':<22}{result.llm_calls} "
        + D(f"(≈ ${result.total_cost_usd:.4f})")
    )
    if result.decisions_by_tier:
        tiers = ", ".join(
            f"{k}={v}" for k, v in sorted(result.decisions_by_tier.items())
        )
        click.echo(f"{'Decisions by tier':<22}{tiers}")
    click.echo(f"{'Elapsed':<22}{result.elapsed_seconds:.2f}s")

    if result.fidelity_reports:
        passed = sum(1 for r in result.fidelity_reports if r.passed)
        n = len(result.fidelity_reports)
        ssim = sum(r.ssim for r in result.fidelity_reports) / n
        verdict = G(f"{passed}/{n}") if passed == n else Y(f"{passed}/{n}")
        click.echo(f"{'Oracle':<22}{verdict} passed " + D(f"(mean SSIM={ssim:.3f})"))

    if result.editability_intended_total or result.editability_actual_total:
        if result.editability_passed:
            status = G("ok")
        else:
            status = R("DRIFT")
        click.echo(
            f"{'Editability':<22}{status} "
            + D(f"({result.editability_actual_total}/{result.editability_intended_total} shapes)")
        )
        if result.editability_failing_slides:
            click.echo(
                D("  Slides with dropped shapes: ")
                + ", ".join(str(i) for i in result.editability_failing_slides)
            )
    if result.overflow_elements:
        n = len(result.overflow_elements)
        worst = max(o.overflow_px for o in result.overflow_elements)
        click.echo(
            f"{'Overflow':<22}"
            + Y(f"{n} element(s)")
            + D(f"  worst={worst:.0f}px")
        )
        # Surface up to three atom-aware authoring hints — enough to fix the
        # common case without flooding the summary on a busy deck.
        seen_hints: set[str] = set()
        shown = 0
        for o in result.overflow_elements:
            if not o.hint or o.hint in seen_hints:
                continue
            seen_hints.add(o.hint)
            shown += 1
            if shown > 3:
                click.echo(
                    D(f"  …and {n - shown + 1} more — see overflow_elements in --report-json")
                )
                break
            click.echo(D(f"  slide {o.slide_index + 1} {o.axis}: ") + o.hint)
    if result.unmatched_signatures:
        click.echo(
            D(f"Unmatched signatures  {len(result.unmatched_signatures)} "
              "(run `slidify harvest` to surface candidates)")
        )
    if result.coverage_gaps:
        click.echo(
            D(f"Coverage gaps         {len(result.coverage_gaps)} "
              "(DOM text not in any unit)")
        )
    if result.exclusivity_violations:
        click.echo(
            D(f"Emit duplicates       {len(result.exclusivity_violations)} "
              "(parent+descendant overlap)")
        )
    click.echo(D("─" * 64))


# ---------------------------------------------------------------------------
# prime-atom-cache — populate atom_signatures.json deterministically
# ---------------------------------------------------------------------------


@click.command(name="prime-atom-cache")
@click.option(
    "--source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    multiple=True,
    help=(
        "HTML file to render and walk for atom signatures. May be passed "
        "multiple times to combine signatures from several reference decks "
        "(e.g. --source examples/landing/atoms.html "
        "--source examples/landing/recipes.html). When omitted, a synthetic "
        "table is built from atoms.yaml without spinning up a browser."
    ),
)
@click.option(
    "--no-synthetic-fallback",
    is_flag=True,
    default=False,
    help=(
        "When --source is given, only emit signatures the browser actually "
        "captured. By default any atom in atoms.yaml that the source HTML "
        "doesn't demonstrate gets a synthetic entry so the table stays "
        "complete."
    ),
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Destination JSON. Defaults to "
        "slidify/patterns/data/atom_signatures.json (the in-package table)."
    ),
)
def prime_atom_cache_cmd(
    source: tuple[Path, ...],
    no_synthetic_fallback: bool,
    out_path: Path | None,
) -> None:
    """Build the priming table that backs `infer_atom_id`.

    Two modes:

      * Default (no ``--source``) — walks ``atoms.yaml``, constructs one
        synthetic ``VisualUnit`` per atom id, hashes it. Fast and
        dependency-free; the signatures don't match real browser renders
        though, so implicit inference rarely fires on actual decks. Useful
        for laying rail in CI/sandbox environments without Chromium.

      * ``--source PATH`` — renders the HTML through Playwright + Chromium,
        walks the DOM, clusters into visual units, and hashes every unit
        whose anchor carries ``data-atom``. These signatures match what
        the convert pipeline computes for real decks, so implicit inference
        actually fires. This is the production priming mode.

    Falls back to synthetic if a ``--source`` walk raises (e.g. Chromium
    missing) so the command always produces a usable table.
    """
    import asyncio

    from slidify.atom_inference import (
        build_browser_table,
        build_hybrid_table,
        build_synthetic_table,
    )

    if source:
        sources = list(source)
        try:
            if no_synthetic_fallback:
                table = asyncio.run(build_browser_table(sources))
                click.echo(
                    f"Browser-primed {len(table)} entries from "
                    f"{len(sources)} source(s).",
                    err=True,
                )
            else:
                table = asyncio.run(build_hybrid_table(sources))
                click.echo(
                    f"Hybrid-primed {len(table)} entries from "
                    f"{len(sources)} source(s) (browser + synthetic backstop).",
                    err=True,
                )
        except Exception as e:
            click.echo(
                f"warning: browser priming failed ({type(e).__name__}: {e}); "
                "falling back to synthetic table.",
                err=True,
            )
            table = build_synthetic_table()
    else:
        table = build_synthetic_table()

    payload = {"version": 1, "entries": dict(sorted(table.items()))}

    if out_path is None:
        from importlib import resources

        pkg = resources.files("slidify.patterns.data")
        out_path = Path(str(pkg / "atom_signatures.json"))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    click.echo(f"Wrote {len(table)} entries → {out_path}")


# ---------------------------------------------------------------------------
# Top-level command. We make convert the default action when no subcommand
# is given, so existing usage `slidify deck.html out.pptx` keeps working.
# ---------------------------------------------------------------------------


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-V", "--version", prog_name="slidify")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """slidify — render HTML decks into editable PPTX.

    Quick start:

        slidify deck.html deck.pptx               # convert (default action)
        slidify doctor                            # check system dependencies
        slidify convert slides/ deck.pptx --json  # JSON report on stdout
        slidify compat --level planned            # roadmap of CSS support

    Environment variables (read by ConversionConfig.from_env()):

        SLIDIFY_NO_ORACLE   SLIDIFY_NO_TIER3   SLIDIFY_LOW_MEMORY
        SLIDIFY_NO_FONTS    SLIDIFY_LLM_BACKEND   SLIDIFY_LLM_MODEL
        SLIDIFY_RENDER_CONCURRENCY
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="convert")
@click.argument(
    "input_path",
    type=click.Path(exists=False, dir_okay=True, file_okay=True, path_type=Path),
)
@click.argument("output_pptx", type=click.Path(dir_okay=False, path_type=Path))
@_convert_options
def convert_cmd(
    input_path: Path,
    output_pptx: Path,
    no_tier3: bool,
    no_oracle: bool,
    llm_backend: str | None,
    llm_model: str | None,
    google_project: str | None,
    google_location: str | None,
    render_concurrency: int,
    low_memory: bool,
    no_differential_render: bool,
    no_embed_fonts: bool,
    report_json: Path | None,
    quiet: bool,
    json_out: bool,
    progress: str,
    progress_file: Path | None,
) -> None:
    """Convert INPUT_PATH (file or directory) to OUTPUT_PPTX.

    INPUT_PATH may be:

      * a single .html file (split on `<!DOCTYPE html>` separators)
      * a directory of *.html files (each treated as one slide,
        sorted lexicographically — name them 01.html, 02.html, …)

    Exit codes:
        0  success
        2  conversion error (rendering, LLM, etc.)
        3  editability drift (shapes silently dropped from output)
    """
    _run_convert(
        input_path, output_pptx, no_tier3, no_oracle, llm_backend, llm_model,
        google_project, google_location, render_concurrency, low_memory,
        no_differential_render, no_embed_fonts, report_json,
        quiet, json_out, progress, progress_file,
    )


cli.add_command(harvest)
cli.add_command(guide_cmd)
cli.add_command(prime_atom_cache_cmd)


# ---------------------------------------------------------------------------
# compat — print the CSS / HTML compatibility matrix
# ---------------------------------------------------------------------------


@cli.command(name="compat")
@click.option(
    "--format", "fmt",
    type=click.Choice(["markdown", "json"], case_sensitive=False),
    default="markdown", show_default=True,
)
@click.option(
    "--level",
    type=click.Choice(
        ["native", "raster", "partial", "unsupported", "planned", "all"],
        case_sensitive=False,
    ),
    default="all", show_default=True,
    help="Filter rows by support level (e.g. --level planned to see roadmap).",
)
def compat_cmd(fmt: str, level: str) -> None:
    """Print the CSS / HTML feature compatibility matrix.

    Use this to find out — without reading source — which CSS properties
    survive as editable PPTX, which fall back to a pixel raster, which are
    dropped entirely, and which are on the roadmap.
    """
    from slidify.compat import MATRIX_VERSION, matrix, matrix_summary, to_markdown

    rows = list(matrix())
    if level.lower() != "all":
        rows = [r for r in rows if r.support.value == level.lower()]

    if fmt.lower() == "json":
        import json

        payload = {
            "version": MATRIX_VERSION,
            "summary": matrix_summary(),
            "rows": [
                {
                    "category": r.category,
                    "feature": r.feature,
                    "support": r.support.value,
                    "code_path": r.code_path,
                    "note": r.note,
                    "plan": r.plan,
                }
                for r in rows
            ],
        }
        click.echo(json.dumps(payload, indent=2))
        return

    if level.lower() == "all":
        click.echo(to_markdown())
        return

    # Filtered markdown: render just the selected level as a flat table.
    lines = [
        f"# slidify compat — `{level}` rows (matrix v{MATRIX_VERSION})",
        "",
        "| Category | Feature | Notes | Plan (if planned) |",
        "|----------|---------|-------|--------------------|",
    ]
    for r in rows:
        plan = (r.plan or "").replace("|", "\\|")
        note = r.note.replace("|", "\\|")
        feature = r.feature.replace("|", "\\|")
        lines.append(f"| {r.category} | `{feature}` | {note} | {plan} |")
    click.echo("\n".join(lines))


# ---------------------------------------------------------------------------
# capture-gif — render an animated HTML slide as an animated GIF
# ---------------------------------------------------------------------------


@cli.command(name="capture-gif")
@click.argument(
    "html_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--out", "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output GIF path. Defaults to <html>.gif next to the source.",
)
@click.option(
    "--duration", "duration_ms", type=int, default=None,
    help="Capture duration in ms. Overrides <meta name='slidify-capture-duration'>.",
)
@click.option(
    "--fps", type=int, default=None,
    help="Frames per second. Overrides <meta name='slidify-capture-fps'>.",
)
@click.option(
    "--width", type=int, default=1280, show_default=True,
    help="Viewport width in CSS pixels.",
)
@click.option(
    "--height", type=int, default=720, show_default=True,
    help="Viewport height in CSS pixels.",
)
def capture_gif_cmd(
    html_path: Path,
    out_path: Path | None,
    duration_ms: int | None,
    fps: int | None,
    width: int,
    height: int,
) -> None:
    """Capture an animated HTML slide as an animated GIF.

    Loads HTML_PATH in headless Chromium *without* the slidify animation
    freeze, samples frames at the declared duration / fps (read from
    <meta> tags or overridden via flags), and writes an optimized
    animated GIF that PowerPoint replays on slideshow.

    The resulting GIF can be referenced from a normal slide as
    <img src="..."> — slidify embeds it as a NativePicture, which
    preserves the animation.
    """
    from slidify.anim_capture import capture_html_to_gif_sync

    if out_path is None:
        out_path = html_path.with_suffix(".gif")
    capture_html_to_gif_sync(
        html_path, out_path,
        duration_ms=duration_ms, fps=fps,
        viewport=(width, height),
    )
    click.echo(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KiB)")


# ---------------------------------------------------------------------------
# version / doctor — environment introspection
# ---------------------------------------------------------------------------


@cli.command(name="version")
@click.option("--json", "json_out", is_flag=True, help="Print machine-readable JSON.")
def version_cmd(json_out: bool) -> None:
    """Print slidify version + Python + key runtime versions."""
    import platform

    info = {
        "slidify": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for pkg in ("playwright", "pptx", "PIL", "anthropic", "google.genai"):
        try:
            mod = __import__(pkg)
            info[pkg] = getattr(mod, "__version__", "unknown")
        except Exception:
            info[pkg] = "not installed"

    if json_out:
        click.echo(json.dumps(info, indent=2))
        return
    click.echo(f"slidify {info['slidify']} (python {info['python']})")
    click.echo(f"platform: {info['platform']}")
    for k in ("playwright", "pptx", "PIL", "anthropic", "google.genai"):
        click.echo(f"  {k:<16}{info[k]}")


@cli.command(name="check")
@click.argument(
    "html_path",
    type=click.Path(exists=True, dir_okay=False, file_okay=True, path_type=Path),
)
@click.option(
    "--json", "json_out", is_flag=True,
    help="Emit JSON to stdout. Pipes cleanly into jq / scripts.",
)
@click.option(
    "--deep", "deep", is_flag=True,
    help="Run the full convert pipeline (Chromium + matcher) with write=False "
         "so the report includes native_area_ratio + matcher decisions. "
         "Slower (1-3s/slide); intended for CI / corpus runs.",
)
def check_cmd(html_path: Path, json_out: bool, deep: bool) -> None:
    """Pre-flight static checker for slide HTML.

    Tells the caller whether the HTML will convert cleanly to PPTX
    BEFORE running the full pipeline. Designed for two consumers:

      * LLMs in an authoring loop — `slidify check slide.html --json`
        in <100ms, reject + regenerate on any external_assets or
        risky_css that lacks a `data-slidify-allow-raster` opt-in.
      * CI corpus runs — `slidify check slide.html --deep --json`
        gives the matcher's actual decisions and predicted
        native_area_ratio.
    """
    from slidify.checker import check_html, check_html_deep

    html = html_path.read_text(encoding="utf-8")
    rep = check_html_deep(html) if deep else check_html(html)
    payload = rep.to_dict()

    if json_out:
        click.echo(json.dumps(payload, indent=2))
        # Exit nonzero if anything is broken — lets `&&` chains gate.
        if not rep.self_contained or rep.risky_css:
            sys.exit(2)
        return

    # Human-readable output.
    if rep.self_contained and not rep.risky_css and not rep.warnings:
        click.echo(click.style("✓ self-contained, no risky CSS, no warnings.", fg="green"))
    else:
        if not rep.self_contained:
            click.echo(click.style("⚠ NOT self-contained:", fg="red"))
            for a in rep.external_assets:
                click.echo(f"  - {a.kind:<14} {a.url}")
        if rep.risky_css:
            click.echo(click.style("⚠ risky CSS:", fg="yellow"))
            for r in rep.risky_css:
                click.echo(f"  - {r.property:<22} = {r.value[:40]:<40}  ({r.selector})")
                click.echo(f"      {r.reason}")
        if rep.warnings:
            click.echo(click.style("ℹ warnings:", fg="cyan"))
            for w in rep.warnings:
                click.echo(f"  - {w}")
    if rep.atom_hints:
        click.echo("")
        click.echo(f"atom hints declared: {', '.join(rep.atom_hints[:10])}")
    if rep.deep is not None:
        click.echo("")
        d = rep.deep
        click.echo(f"native_area_ratio: {d['native_area_ratio']:.4f}")
        click.echo(f"slides           : {d['n_slides']}")
        if d.get("unmatched_signatures"):
            click.echo(f"unmatched sigs   : {len(d['unmatched_signatures'])}")
        er = d.get("escape_rate") or {}
        if er.get("value", 0) > 0:
            click.echo(f"escape_rate      : {er['value']:.4f}")
    if not rep.self_contained or rep.risky_css:
        sys.exit(2)


@cli.command(name="doctor")
@click.option("--json", "json_out", is_flag=True, help="Print machine-readable JSON.")
def doctor_cmd(json_out: bool) -> None:
    """Verify that the runtime environment can convert decks end-to-end.

    Checks for:
      * LibreOffice (oracle pass: PPTX → PNG)
      * tesseract (oracle OCR recall)
      * poppler-utils' `pdftoppm` (LibreOffice fallback path)
      * Playwright's bundled Chromium
      * at least one LLM backend (env vars)
      * Inter font (default deck typeface)

    Exits 0 if every required check passes, 1 otherwise (LLM / fonts are
    optional and never fail the doctor).
    """
    import os

    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, *, optional: bool = False) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "optional": optional})

    # Binaries
    for binary, label, required in [
        ("soffice", "LibreOffice (oracle)", True),
        ("libreoffice", "LibreOffice (alt)", False),
        ("tesseract", "Tesseract OCR", True),
        ("pdftoppm", "poppler-utils pdftoppm", True),
    ]:
        path = shutil.which(binary)
        check(label, path is not None, path or "missing on PATH", optional=not required)

    # Playwright + Chromium
    try:
        from playwright._impl._driver import compute_driver_executable  # type: ignore

        compute_driver_executable()
        check("Playwright driver", True, "available", optional=False)
    except Exception as e:
        check("Playwright driver", False, f"{type(e).__name__}: {e}", optional=False)

    chromium_dir = Path.home() / ".cache" / "ms-playwright"
    if env := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        chromium_dir = Path(env)
    has_chromium = chromium_dir.is_dir() and any(chromium_dir.glob("chromium-*"))
    check(
        "Playwright Chromium",
        has_chromium,
        str(chromium_dir) if has_chromium else (
            f"not found at {chromium_dir} — run `playwright install --with-deps chromium`"
        ),
        optional=False,
    )
    if has_chromium:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.set_content("<!doctype html><title>slidify doctor</title>")
                page.close()
                browser.close()
            check("Chromium launch", True, "smoke render passed", optional=False)
        except Exception as e:
            detail = (
                f"{type(e).__name__}: {str(e).splitlines()[0]} — "
                "run `make playwright-deps` or `playwright install --with-deps chromium`; "
                "containerized/sandboxed shells may need the command outside the sandbox"
            )
            check("Chromium launch", False, detail, optional=False)

    # LLM backends — any one is enough; none is acceptable (raster fallback).
    llm_present = any([
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        os.environ.get("ANTHROPIC_API_KEY"),
        os.environ.get("GOOGLE_CLOUD_PROJECT"),
    ])
    detected: list[str] = []
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        detected.append("gemini-aistudio")
    if os.environ.get("ANTHROPIC_API_KEY"):
        detected.append("anthropic")
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        detected.append("vertex (gemini/claude)")
    check(
        "LLM backend",
        llm_present,
        ", ".join(detected) if detected else "none — tier 3 falls back to Raster",
        optional=True,
    )

    # Inter font (default deck face).
    inter_present = False
    try:
        from slidify.font_embed import discover_inter

        inter_present = discover_inter() is not None
    except Exception:
        pass
    check(
        "Inter font",
        inter_present,
        "found" if inter_present else "missing — install fonts-inter",
        optional=True,
    )

    if json_out:
        click.echo(json.dumps({"checks": checks}, indent=2))
    else:
        for c in checks:
            mark = (
                click.style("✓", fg="green") if c["ok"]
                else click.style("?", fg="yellow") if c["optional"]
                else click.style("✗", fg="red")
            )
            label = c["name"]
            tag = click.style(" (optional)", dim=True) if c["optional"] else ""
            click.echo(f"  {mark}  {label:<26}{tag} {click.style(c['detail'], dim=True)}")

    failed_required = [c for c in checks if not c["ok"] and not c["optional"]]
    if failed_required:
        if not json_out:
            click.echo()
            click.echo(click.style("Missing required dependencies. See README §Install.", fg="red"))
        sys.exit(1)


# ---------------------------------------------------------------------------
# manifest — machine-readable description of the CLI for LLM agents
# ---------------------------------------------------------------------------


_MANIFEST: dict = {
    "tool": "slidify",
    "version": __version__,
    "summary": "Convert HTML decks to editable PPTX.",
    "exit_codes": {
        "0": "success",
        "1": "doctor: required system dependency missing",
        "2": "conversion error (input not found, render/LLM failure)",
        "3": "editability drift — shapes silently dropped from output",
    },
    "env_vars": {
        "SLIDIFY_NO_ORACLE":         "1 to skip the LibreOffice fidelity oracle",
        "SLIDIFY_NO_TIER3":          "1 to skip the LLM adjudicator",
        "SLIDIFY_LOW_MEMORY":        "1 to drop per-slide state right after emit",
        "SLIDIFY_NO_FONTS":          "1 to skip embedding source fonts",
        "SLIDIFY_LLM_BACKEND":       "gemini-aistudio | gemini-vertex | anthropic | claude-vertex",
        "SLIDIFY_LLM_MODEL":         "model id override for the chosen backend",
        "SLIDIFY_RENDER_CONCURRENCY":"int, slides rendered in parallel (default 4)",
        "ANTHROPIC_API_KEY":         "credential for `anthropic` backend",
        "GEMINI_API_KEY":            "credential for `gemini-aistudio` backend",
        "GOOGLE_CLOUD_PROJECT":      "GCP project for Vertex backends",
        "GOOGLE_CLOUD_LOCATION":     "GCP region for Vertex backends (e.g. us-east5)",
    },
    "commands": [
        {
            "name": "convert",
            "summary": "Convert an HTML deck to PPTX.",
            "usage": "slidify convert <input> <output.pptx> [options]",
            "args": [
                {"name": "input", "type": "path|-", "desc": "HTML file, directory of HTML files, or `-` for stdin"},
                {"name": "output", "type": "path", "desc": "Destination .pptx path"},
            ],
            "options": [
                {"name": "--no-oracle",            "desc": "skip fidelity oracle (faster, no SSIM/OCR check)"},
                {"name": "--no-tier3",             "desc": "skip LLM adjudicator (raster fallback for ambiguous units)"},
                {"name": "--low-memory",           "desc": "drop per-slide state after emit; disables auto-correct"},
                {"name": "--no-embed-fonts",       "desc": "skip embedding source fonts"},
                {"name": "--no-differential-render","desc": "skip the second decoration-only screenshot"},
                {"name": "--render-concurrency N", "desc": "slides rendered in parallel (default 4)"},
                {"name": "--llm-backend X",        "desc": "force LLM backend (auto-detected otherwise)"},
                {"name": "--llm-model X",          "desc": "override default model"},
                {"name": "--report-json PATH",     "desc": "write full ConversionResult to PATH"},
                {"name": "--quiet, -q",            "desc": "print only the output path"},
                {"name": "--json",                 "desc": "print the ConversionResult as JSON to stdout"},
            ],
            "examples": [
                {"desc": "Convert a single file",
                 "cmd":  "slidify convert deck.html deck.pptx"},
                {"desc": "Fast preview (no oracle, no LLM)",
                 "cmd":  "slidify convert deck.html out.pptx --no-oracle --no-tier3"},
                {"desc": "Convert from stdin",
                 "cmd":  "cat deck.html | slidify convert - out.pptx --json"},
                {"desc": "Convert a directory of per-slide files",
                 "cmd":  "slidify convert slides/ deck.pptx"},
            ],
        },
        {
            "name": "harvest",
            "summary": "Run a corpus and emit clustered Tier-0 candidate atoms.",
            "usage": "slidify harvest <dir> [--output PATH] [--top-n N] [--min-occurrences N]",
            "examples": [
                {"desc": "Mine a corpus and write clusters.json",
                 "cmd":  "slidify harvest examples/corpus/ --output clusters.json --top-n 50"},
            ],
        },
        {
            "name": "compat",
            "summary": "Print the CSS/HTML compatibility matrix.",
            "usage": "slidify compat [--format markdown|json] [--level native|raster|partial|unsupported|planned|all]",
            "examples": [
                {"desc": "Find what slidify currently rasters",
                 "cmd":  "slidify compat --level raster"},
            ],
        },
        {
            "name": "capture-gif",
            "summary": "Render an animated HTML slide to an animated GIF.",
            "usage": "slidify capture-gif <html> [--out FILE] [--duration MS] [--fps N]",
        },
        {
            "name": "doctor",
            "summary": "Verify system deps (LibreOffice, tesseract, Chromium, fonts).",
            "usage": "slidify doctor [--json]",
        },
        {
            "name": "version",
            "summary": "Print version of slidify and key runtime libraries.",
            "usage": "slidify version [--json]",
        },
        {
            "name": "manifest",
            "summary": "Print this manifest (machine-readable CLI surface).",
            "usage": "slidify manifest [<command>] [--brief]",
            "examples": [
                {"desc": "Just the command index", "cmd": "slidify manifest --brief"},
                {"desc": "Full spec for one command", "cmd": "slidify manifest convert"},
            ],
        },
        {
            "name": "guide",
            "summary": "Read shipped long-form guides with built-in section/grep helpers.",
            "usage": "slidify guide [<topic>] [--toc] [--section H] [--grep RX] [--search RX]",
            "examples": [
                {"desc": "List all guides", "cmd": "slidify guide"},
                {"desc": "Read one", "cmd": "slidify guide authoring"},
                {"desc": "Just the headings", "cmd": "slidify guide authoring --toc"},
                {"desc": "One section", "cmd": "slidify guide authoring --section 'What forces a raster'"},
                {"desc": "Grep across all guides", "cmd": "slidify guide --search 'tier 0'"},
            ],
        },
        {
            "name": "field",
            "summary": "Extract a dotted field from a JSON file (built-in jq-lite).",
            "usage": "slidify field <json_path> <dotted.path>",
            "examples": [
                {"desc": "Single scalar", "cmd": "slidify field report.json native_area_ratio"},
                {"desc": "Nested list",   "cmd": "slidify field report.json fidelity_reports.0.ssim"},
            ],
        },
    ],
    "stable_output_schema": {
        "convert --json": {
            "pptx_path":                  "string",
            "n_slides":                   "int",
            "native_area_ratio":          "float (0..1, higher = more editable)",
            "pattern_coverage":           "float (0..1)",
            "cache_hit_rate":             "float (0..1)",
            "llm_calls":                  "int",
            "total_cost_usd":             "float",
            "elapsed_seconds":            "float",
            "decisions_by_tier":          "dict[str, int]",
            "fidelity_reports":           "list — per-slide SSIM/OCR scores",
            "editability_passed":         "bool — true if every intended shape survived",
            "editability_failing_slides": "list[int]",
            "unmatched_signatures":       "list — Tier-0 candidates",
        },
    },
    "tips_for_agents": [
        "Always run `slidify doctor --json` once before a session to confirm the environment.",
        "Pipe HTML through stdin for one-shot conversions; no temp files needed.",
        "Use `--json` for programmatic output. Errors come back as `{error, type, stage, _remediation[]}`.",
        "Successful `--json` results include a `_next` array — concrete follow-up commands tailored to this run.",
        "Don't shell out to `jq` — use `slidify field <report.json> <dotted.path>`.",
        "Don't shell out to `head`/`grep` for guides — use `slidify guide <topic> --section/--toc/--grep`.",
        "Drill into the manifest progressively: `slidify manifest --brief` → `slidify manifest convert`.",
        "Use `--no-oracle --no-tier3` for fast iteration; turn them on for the final emit.",
        "If `editability_passed` is false, inspect `editability_failing_slides` and re-emit those.",
        "When authoring slides, read `slidify guide authoring` first — every shipped guide is tuned for native_area_ratio.",
    ],
}


@cli.command(name="manifest")
@click.argument("command", required=False)
@click.option(
    "--brief",
    is_flag=True,
    help="Drop schemas + tips_for_agents — just the command index.",
)
def manifest_cmd(command: str | None, brief: bool) -> None:
    """Print a machine-readable description of the CLI surface.

    Progressive disclosure:

    * `slidify manifest`           — full manifest
    * `slidify manifest --brief`   — command index only (cheap to load)
    * `slidify manifest convert`   — spec for one command (drill-down)

    Designed for LLM agents to introspect before invoking slidify: every
    subcommand, option, env var, exit code and example is listed, plus a
    stable schema for `--json` payloads so callers can rely on field names
    without reading source.
    """
    if command:
        for c in _MANIFEST["commands"]:
            if c["name"] == command:
                click.echo(json.dumps(c, indent=2))
                return
        click.echo(json.dumps(
            {"error": f"unknown command: {command}",
             "known": [c["name"] for c in _MANIFEST["commands"]]},
            indent=2,
        ))
        sys.exit(2)

    if brief:
        click.echo(json.dumps({
            "tool": _MANIFEST["tool"],
            "version": _MANIFEST["version"],
            "summary": _MANIFEST["summary"],
            "commands": [
                {"name": c["name"], "summary": c["summary"]}
                for c in _MANIFEST["commands"]
            ],
            "_next": [
                "slidify manifest <command>  # drill into one command",
                "slidify guide               # list of long-form guides",
                "slidify doctor --json       # verify environment",
            ],
        }, indent=2))
        return

    click.echo(json.dumps(_MANIFEST, indent=2))


# ---------------------------------------------------------------------------
# field — pull a single dotted path out of a JSON file (built-in jq-lite,
# so binary distributions without jq still work).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# report-escape-clusters — CONTRACT-v2 §F.5 promotion-loop scaffold
# ---------------------------------------------------------------------------
#
# When the harvester clusters escape-hatch usage by intent and a cluster
# crosses a threshold (default ≥15 instances), it auto-files a GitHub issue
# tagged `atom-proposal` for the designer to review. v1 of this command
# only PRINTS what it would file — actual GitHub integration is M4 follow-up.
#
# TODO(M4): wire to gh CLI / GitHub REST. Cluster signature, sample CSS
# payloads, draft manifest row → `atom-proposal` issue per cluster.

@cli.command(name="report-escape-clusters")
@click.argument(
    "report_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--threshold",
    type=int,
    default=15,
    show_default=True,
    help="Minimum cluster size before a promotion would be proposed.",
)
@click.option(
    "--auto-file-issues",
    is_flag=True,
    help="(stub) Would file `atom-proposal` issues to GitHub. v1 only logs "
    "'would file'; actual gh integration is M4 follow-up.",
)
def report_escape_clusters_cmd(
    report_path: Path | None, threshold: int, auto_file_issues: bool
) -> None:
    """Inspect `escape_rate.byIntent` clusters; surface promotion candidates.

    Per CONTRACT-v2 §F.5. The harvester clusters escape-hatch usage across a
    corpus and promotes intents that cross a threshold to atom proposals.

    \b
    v1 (this command): prints what would be filed.
    v2 (M4 follow-up): actually files `atom-proposal` GitHub issues.

    \b
    Examples:
        slidify report-escape-clusters report.json
        slidify report-escape-clusters report.json --threshold 25
        slidify report-escape-clusters report.json --auto-file-issues  # stub
    """
    if report_path is None:
        click.echo(
            "slidify: pass a report.json path "
            "(produced by `slidify convert ... --report-json report.json`).",
            err=True,
        )
        sys.exit(2)

    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        click.echo(f"slidify: invalid JSON: {e}", err=True)
        sys.exit(2)

    escape = data.get("escape_rate", {}) or data.get("escapeRate", {})
    by_intent: dict[str, float] = escape.get("byIntent", {}) or {}
    total_value: float = float(escape.get("value", 0.0))

    if not by_intent:
        click.echo(
            "No escape-hatch usage recorded in this report. "
            "Either the deck used no `chrome.escape-hatch` atoms or "
            "the escape-rate metering wasn't populated."
        )
        return

    click.echo(f"Escape rate: {total_value:.4f} (deck-wide area share)")
    click.echo(f"Threshold  : {threshold} (intent count)")
    click.echo("─" * 64)

    # Prefer the count-based comparison when the report carries counts
    # (post-compile_ir.to_report_dict update). Fall back to area share
    # only when older reports omit `countByIntent`. This makes
    # `--threshold` an actually-functional knob instead of a no-op.
    count_by_intent = escape.get("countByIntent") or escape.get("count_by_intent") or {}

    if count_by_intent:
        candidates = sorted(
            count_by_intent.items(), key=lambda kv: -kv[1]
        )
        for intent, count in candidates:
            share = float(by_intent.get(intent, 0.0))
            would_promote = int(count) >= int(threshold)
            marker = (
                click.style("→ would-promote", fg="yellow")
                if would_promote else "  observe"
            )
            click.echo(
                f"  {intent:<32}{int(count):>5d}× ({share:>6.4f})  {marker}"
            )
            if would_promote and auto_file_issues:
                # TODO(M4): replace with `gh issue create --label atom-proposal ...`
                click.echo(
                    click.style(
                        f"    [would file] atom-proposal issue for "
                        f"intent={intent} count={int(count)} share={share:.4f}",
                        dim=True,
                    )
                )
    else:
        # Backward-compat: report.json predates `countByIntent`. Fall back
        # to area-share threshold (1% floor); document the limitation.
        click.echo(
            click.style(
                "warning: report.json lacks 'countByIntent'; falling back "
                "to 1%-area-share floor (recompile with current slidify "
                "to use --threshold).",
                fg="yellow",
            ),
            err=True,
        )
        candidates = sorted(by_intent.items(), key=lambda kv: -kv[1])
        for intent, share in candidates:
            would_promote = share >= 0.01
            marker = (
                click.style("→ would-promote", fg="yellow")
                if would_promote else "  observe"
            )
            click.echo(f"  {intent:<32}{share:>8.4f}    {marker}")
            if would_promote and auto_file_issues:
                click.echo(
                    click.style(
                        f"    [would file] atom-proposal issue for intent={intent}",
                        dim=True,
                    )
                )


@cli.command(name="field")
@click.argument("json_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("dotted_path")
def field_cmd(json_path: Path, dotted_path: str) -> None:
    """Extract a dotted field from a JSON file.

    Numeric segments index lists; string segments index dicts.

    \b
    Examples:
        slidify field report.json native_area_ratio
        slidify field report.json fidelity_reports.0.ssim
        slidify field report.json decisions_by_tier.tier0
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        click.echo(f"slidify: invalid JSON: {e}", err=True)
        sys.exit(2)

    cur = data
    for seg in dotted_path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError) as e:
                click.echo(f"slidify: cannot index {type(cur).__name__} with '{seg}': {e}", err=True)
                sys.exit(2)
        elif isinstance(cur, dict):
            if seg not in cur:
                click.echo(
                    f"slidify: key '{seg}' not in object (have: {', '.join(sorted(cur.keys()))})",
                    err=True,
                )
                sys.exit(2)
            cur = cur[seg]
        else:
            click.echo(f"slidify: cannot descend into {type(cur).__name__} at '{seg}'", err=True)
            sys.exit(2)

    if isinstance(cur, (dict, list)):
        click.echo(json.dumps(cur, indent=2))
    else:
        click.echo(str(cur))


def main() -> None:
    """Entry point shim: when called with positional args that look like
    (input, output), dispatch to convert directly so the legacy
    `slidify foo.html bar.pptx` invocation keeps working."""
    args = sys.argv[1:]
    known_subs = {
        "convert", "harvest", "compat", "capture-gif",
        "check",
        "doctor", "version", "manifest", "guide", "field",
        "report-escape-clusters",
        "prime-atom-cache",
        "--help", "-h", "--version", "-V",
    }
    if args and not args[0].startswith("-") and args[0] not in known_subs:
        # Implicit convert: rewrite to `slidify convert ...`
        sys.argv = [sys.argv[0], "convert", *args]
    cli()


if __name__ == "__main__":  # pragma: no cover
    main()
