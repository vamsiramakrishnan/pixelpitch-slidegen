# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Engagement wiring tests: catalog cache, expression seeding, companion,
dino fragment, reveal token. The heavy pipeline is stubbed; only the wiring
under test runs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import tools
from app.companion import CompanionThrottle, companion_line, dino_fragment
from app.deck_run import slug_for


def _ctx(session_id: str = "engagement-1"):
    return SimpleNamespace(session=SimpleNamespace(id=session_id))


@pytest.fixture(autouse=True)
def _clean_catalog_cache(monkeypatch):
    monkeypatch.setattr(tools, "_CATALOG_CACHE", {})
    yield


def test_catalog_resolves_once_then_caches(monkeypatch):
    calls = {"n": 0}

    def fake_resolve(slug):
        calls["n"] += 1
        return ([{"id": f"{slug}-1", "world": "w", "archetype": "a"}], [])

    monkeypatch.setattr(tools, "_resolve_expression_catalog", fake_resolve)
    first = tools._load_expression_catalog("Example Retail 100Y")
    second = tools._load_expression_catalog("example-retail 100y")
    assert first is second
    assert calls["n"] == 1


def test_expression_ids_reach_the_brief(monkeypatch):
    briefs = []

    def fake_turn(*, workdir, message, **kwargs):
        briefs.append((workdir / "brief.md").read_text(encoding="utf-8"))
        return "done"

    monkeypatch.setattr("app.deck_run.run_turn", fake_turn)

    # Short-circuit the pipeline right after authoring: renderer unconfigured.
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)
    monkeypatch.setattr(
        tools,
        "_load_expression_catalog",
        lambda _name: (
            [
                {
                    "id": "white-ledger-timeline-assertion",
                    "world": "Editorial Light",
                    "archetype": "ledger_timeline",
                }
            ],
            [],
        ),
    )

    result = tools._generate_deck_sync(
        topic="centenary",
        audience="team",
        goal="celebrate",
        slide_count=1,
        brand_id="stripe",
        expression_ids="white-ledger-timeline-assertion",
        tool_context=_ctx(),  # type: ignore[arg-type]
    )
    assert len(briefs) == 1
    assert "Chosen visual expressions" in briefs[0]
    assert "white-ledger-timeline-assertion" in briefs[0]
    assert "Editorial Light / ledger_timeline" in briefs[0]
    # The turn wrote nothing, so the lane reports that rather than a crash.
    assert result["status"] == "error"


def test_dino_fragment_cached_and_progress_carries_token(monkeypatch):
    monkeypatch.setattr("app.deck_run.run_turn", fake_turn_writes_one_slide)
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)

    ctx = _ctx("engagement-dino")
    tools._generate_deck_sync(
        topic="t", audience="a", goal="g",
        slide_count=1,
        brand_id="stripe",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    fragments = tools.get_cached_fragments("session:engagement-dino")
    assert "__DINO_HTML__" in fragments
    assert "canvas" in fragments["__DINO_HTML__"]


def fake_turn_writes_one_slide(*, workdir, message, **kwargs):
    (workdir / "slides").mkdir(exist_ok=True)
    (workdir / "slides" / "01-t.html").write_text(
        "<html><body><h1 data-pptx-role='title'>T</h1></body></html>", encoding="utf-8"
    )
    return "done"


def test_companion_throttle_suppresses_noise():
    throttle = CompanionThrottle(min_interval_seconds=10)
    import time

    t0 = time.monotonic()
    assert throttle.allow(t0) is not None
    assert throttle.allow(t0 + 1) is None
    assert throttle.allow(t0 + 11) is not None


def test_companion_lines_carry_real_context():
    line = companion_line("tool", tool="run_command", tick=0)
    assert "run_command" in line
    line = companion_line("slide_ready", slide=4, tick=2)
    assert "slide 4" in line.lower()


def test_dino_fragment_is_self_contained():
    frag = dino_fragment("hello pet")
    for needle in ("canvas", "requestAnimationFrame", "hello pet"):
        assert needle in frag
    assert "http://" not in frag and "https://" not in frag


def test_maybe_build_catalog_decision_logic(monkeypatch):
    submitted = []
    monkeypatch.setattr(tools, "_CATALOG_CACHE", {})
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", set())
    monkeypatch.setattr(
        tools, "_PIPELINE_POOL", SimpleNamespace(submit=lambda fn, *a, **k: submitted.append((fn, a)))
    )
    monkeypatch.setattr(tools, "_catalog_exists_in_gcs", lambda slug: False)

    def build_finished(slug: str) -> None:
        """What the real build's finally clause does when a turn ends.

        The fake pool above never runs _build_catalog, so each case below has
        to release the slug itself to stand on its own.
        """
        tools._CATALOG_BUILDS_IN_FLIGHT.discard(slug)

    # enabled + missing -> enqueued exactly once
    assert tools.maybe_build_catalog("gs://b/t.pptx", "Example Retail 100Y") is True
    assert len(submitted) == 1

    # disabled -> never enqueues
    monkeypatch.setenv("SLIDEGEN_AUTO_PRECOMPUTE", "0")
    assert tools.maybe_build_catalog("gs://b/t.pptx", "Other") is False
    assert len(submitted) == 1

    # A retry landing mid-build reports the build, it does not start another.
    monkeypatch.setenv("SLIDEGEN_AUTO_PRECOMPUTE", "1")
    assert tools.maybe_build_catalog("gs://b/t.pptx", "Example Retail 100Y") is True
    assert len(submitted) == 1

    # A catalog alone is not creative admission; missing baselines rebuild.
    build_finished(slug_for("Example Retail 100Y"))
    monkeypatch.setattr(tools, "_catalog_exists_in_gcs", lambda slug: True)
    monkeypatch.setattr("app.deck_run.prepared_bundle_exists", lambda *args: False)
    assert tools.maybe_build_catalog("gs://b/t.pptx", "Example Retail 100Y") is True
    assert len(submitted) == 2

    # Catalog + manifest-valid guarded baselines -> skipped.
    monkeypatch.setattr("app.deck_run.prepared_bundle_exists", lambda *args: True)
    assert tools.maybe_build_catalog("gs://b/t.pptx", "Example Retail 100Y") is False
    assert len(submitted) == 2
