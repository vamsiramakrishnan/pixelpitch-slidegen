"""Round-trip pattern fixture tests.

Loads `expected.yaml`, renders each fixture HTML through the renderer +
clusterer + Tier-0 matcher, and asserts at least one of the `expect_any`
recipe IDs fires.

Each fixture is a marker-test that fails CI if a pattern's predicate is
weakened or its priority changes such that the fixture no longer matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from slidify.patterns import (
    PatternStats,
    classify_tier0,
    get_default_catalog,
)
from slidify.renderer import Renderer
from slidify.units import cluster, flatten

FIXTURES_PATH = Path(__file__).parent / "expected.yaml"


def _load_fixtures() -> list[dict]:
    raw = yaml.safe_load(FIXTURES_PATH.read_text(encoding="utf-8"))
    return raw.get("fixtures", [])


@pytest.fixture
async def renderer():
    """Per-test renderer. Module-scoped async fixtures don't compose cleanly
    with pytest-asyncio's auto mode in this version; per-test launches add
    ~1s of Chromium startup overhead but make the suite robust."""
    r = Renderer()
    await r.start()
    try:
        yield r
    finally:
        await r.close()


@pytest.fixture
def catalog():
    return get_default_catalog()


@pytest.mark.slow
@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["id"])
async def test_pattern_round_trip(fixture: dict, renderer, catalog):
    rendered = await renderer.render(fixture["html"])
    assert not rendered.degraded, f"renderer degraded: {rendered.reason}"

    roots = cluster(rendered.elements)
    flat = flatten(roots)
    stats = PatternStats()
    for u in flat:
        classify_tier0(u, catalog, stats=stats)

    expected = set(fixture["expect_any"])
    matched = set(stats.hits_by_id.keys()) | set(stats.structural_hits_by_id.keys())
    common = expected & matched
    assert common, (
        f"Fixture '{fixture['id']}' did not match any of {expected}. "
        f"Matched recipes: {sorted(matched)}"
    )
