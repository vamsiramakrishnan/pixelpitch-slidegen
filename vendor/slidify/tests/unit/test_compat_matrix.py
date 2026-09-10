"""Self-test for the CSS / HTML compatibility matrix.

The matrix is documentation that has to stay aligned with code: each row
cites a dotted Python path that's supposed to implement it. If a refactor
deletes / renames the cited path without updating the matrix, this test
fails — keeping the matrix from rotting into lies.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from slidify.cli import cli
from slidify.compat import (
    MATRIX,
    MATRIX_VERSION,
    Support,
    code_path_exists,
    matrix_summary,
    to_markdown,
)


def test_every_code_path_resolves():
    missing = [r for r in MATRIX if not code_path_exists(r.code_path)]
    assert not missing, (
        "Compat matrix rows cite code paths that don't exist:\n"
        + "\n".join(f"  - {r.feature}: {r.code_path}" for r in missing)
    )


def test_matrix_is_non_empty_and_versioned():
    assert MATRIX_VERSION
    assert len(MATRIX) >= 20, "matrix is suspiciously sparse"


def test_summary_counts_match_rows():
    summary = matrix_summary()
    assert sum(summary.values()) == len(MATRIX)
    for s in Support:
        assert s.value in summary


def test_to_markdown_renders_each_row():
    md = to_markdown()
    for row in MATRIX:
        assert row.feature in md, f"missing row: {row.feature}"


def test_cli_compat_markdown():
    runner = CliRunner()
    result = runner.invoke(cli, ["compat", "--format", "markdown"])
    assert result.exit_code == 0, result.output
    assert "compatibility" in result.output.lower()
    # At least one categorized table.
    assert "| Feature | Support |" in result.output


def test_cli_compat_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["compat", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["version"] == MATRIX_VERSION
    assert len(payload["rows"]) == len(MATRIX)
    # Each row carries the four schema keys.
    for r in payload["rows"]:
        assert {"category", "feature", "support", "code_path", "note"} <= r.keys()


def test_table_row_documents_native_table():
    """Sanity-check that the new table support actually appears in the matrix."""
    rows = [r for r in MATRIX if "<table>" in r.feature]
    assert len(rows) == 1
    assert rows[0].support == Support.Native


def test_planned_rows_have_a_plan_and_no_code_path():
    """A `Planned` row is a roadmap commitment — it must say HOW we'll ship it
    and must not lie about having an existing code path."""
    planned = [r for r in MATRIX if r.support == Support.Planned]
    assert planned, "matrix should advertise at least one roadmap item"
    for r in planned:
        assert r.plan, f"Planned row missing a plan: {r.feature}"
        assert not r.code_path, (
            f"Planned row should not cite a code path: {r.feature} "
            f"(cites {r.code_path!r})"
        )


def test_non_planned_rows_have_a_code_path():
    """Conversely, a shipped row (any non-Planned level) must cite an
    actual code path so the self-test can catch refactor drift."""
    for r in MATRIX:
        if r.support == Support.Planned:
            continue
        assert r.code_path, f"non-Planned row missing code_path: {r.feature}"


def test_matrix_covers_ambitious_categories():
    """The matrix should cover the categories that distinguish brilliant
    presentations: Notion-style blocks, motion (Framer/Lottie), 3D/WebGL,
    video embeds, and charts. This is a documentation contract — if a
    category gets dropped from the matrix, this test fails loudly."""
    must_cover = {
        "Block layouts",
        "Motion",
        "3D / WebGL",
        "Embeds",
        "Charts",
        "Math / Code",
        "Fonts / Icons",
    }
    have = {r.category for r in MATRIX}
    missing = must_cover - have
    assert not missing, f"matrix is missing categories: {missing}"


def test_three_d_canvas_has_planned_gif_path():
    """The 'Three.js → GIF embed' upgrade is the headline ambition; ensure
    the matrix advertises it as Planned with a concrete plan."""
    rows = [
        r for r in MATRIX
        if r.category == "3D / WebGL" and "GIF" in r.feature
    ]
    assert rows, "matrix should advertise the 3D-canvas → GIF roadmap"
    assert rows[0].support == Support.Planned
    assert "data-pptx-record" in rows[0].plan or "GIF" in rows[0].plan


def test_cli_compat_filter_by_level_planned():
    runner = CliRunner()
    result = runner.invoke(cli, ["compat", "--format", "json", "--level", "planned"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert all(r["support"] == "planned" for r in payload["rows"])
    assert payload["rows"], "no planned rows surfaced"
