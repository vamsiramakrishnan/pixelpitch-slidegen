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

"""Unit tests for slidegen tools (no LLM involved)."""

import asyncio
import inspect
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app import tools
from app.tools import (
    SLIDE_PREVIEW_TOKEN,
    _build_preview_html,
    _preview_key,
    generate_deck,
    get_cached_fragments,
    list_brands,
    list_styles,
    load_brand_guideline,
    render_deck_to_pptx,
    use_reference_deck,
)


def test_generate_deck_does_not_expose_placeholder_population_mode():
    assert "faithful_fill" not in inspect.signature(generate_deck).parameters


@pytest.mark.asyncio
async def test_pipeline_cancellation_reaches_worker_before_side_effect():
    started = threading.Event()
    leaked_side_effect = threading.Event()

    def blocking_pipeline(cancellation_event=None):
        started.set()
        if cancellation_event is None:
            time.sleep(0.08)
            leaked_side_effect.set()
            return {"status": "ok"}
        cancellation_event.wait(timeout=0.2)
        if not cancellation_event.is_set():
            leaked_side_effect.set()
        return {"status": "cancelled"}

    task = asyncio.create_task(tools._run_in_pipeline_pool(blocking_pipeline))
    await asyncio.to_thread(started.wait, 0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.12)

    assert not leaked_side_effect.is_set()


@pytest.mark.asyncio
async def test_generate_deck_keeps_event_loop_responsive(monkeypatch):
    def blocking_pipeline(**kwargs):
        time.sleep(0.08)
        return {"status": "ok"}

    monkeypatch.setattr("app.tools._generate_deck_sync", blocking_pipeline)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while ticks < 3:
            await asyncio.sleep(0.01)
            ticks += 1

    result, _ = await asyncio.gather(
        generate_deck("t", "a", "g", 1, "stripe"),
        ticker(),
    )

    assert result == {"status": "ok"}
    assert ticks == 3


@pytest.mark.asyncio
async def test_reference_ingest_keeps_event_loop_responsive(monkeypatch):
    def blocking_ingest(**kwargs):
        time.sleep(0.08)
        return {"status": "ok"}

    monkeypatch.setattr("app.tools._use_reference_deck_sync", blocking_ingest)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while ticks < 3:
            await asyncio.sleep(0.01)
            ticks += 1

    result, _ = await asyncio.gather(
        use_reference_deck("gs://example/deck.pptx"), ticker()
    )

    assert result == {"status": "ok"}
    assert ticks == 3


def test_list_brands_returns_curated_set():
    result = list_brands()
    assert result["status"] == "ok"
    ids = [b["id"] for b in result["brands"]]
    assert "stripe" in ids and "apple" in ids
    for brand in result["brands"]:
        assert set(brand) >= {"id", "name", "category", "description"}


def test_renderer_auth_uses_current_workload_identity(monkeypatch):
    monkeypatch.delenv("SLIDEGEN_RENDERER_API_KEY", raising=False)
    monkeypatch.delenv("SLIDEGEN_RENDERER_INVOKER_SA", raising=False)
    tools._ID_TOKEN_CACHE.clear()
    monkeypatch.setattr(
        "google.oauth2.id_token.fetch_id_token",
        lambda _request, audience: f"token-for:{audience}",
    )

    headers = tools._renderer_auth_headers("https://renderer.example")

    assert headers == {
        "Authorization": "Bearer token-for:https://renderer.example"
    }


def test_private_renderer_receives_both_workload_identity_and_api_key(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_RENDERER_API_KEY", "shared-secret")
    monkeypatch.delenv("SLIDEGEN_RENDERER_INVOKER_SA", raising=False)
    tools._ID_TOKEN_CACHE.clear()
    monkeypatch.setattr(
        "google.oauth2.id_token.fetch_id_token",
        lambda *_args: "private-renderer-token",
    )

    assert tools._renderer_auth_headers("https://renderer.example") == {
        "X-API-Key": "shared-secret",
        "Authorization": "Bearer private-renderer-token",
    }


def test_list_styles_is_paginated_by_default():
    first = list_styles()
    second = list_styles(page=2)

    assert first["status"] == "ok"
    assert first["style_count"] == 8
    assert first["total_style_count"] > first["style_count"]
    assert first["has_more"] is True
    assert first["next_page"] == 2
    assert second["page"] == 2
    assert {style["id"] for style in first["styles"]}.isdisjoint(
        style["id"] for style in second["styles"]
    )


def test_list_styles_filters_before_paginating():
    result = list_styles(query="Swiss", page_size=4)

    assert result["status"] == "ok"
    assert result["query"] == "Swiss"
    assert 0 < result["style_count"] <= 4
    assert all(
        "swiss"
        in " ".join(
            [
                style["id"],
                style["name"],
                style["tagline"],
                *style["mood"],
                *style["fonts"],
            ]
        ).casefold()
        for style in result["styles"]
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page": 0}, "page must be at least 1"),
        ({"page_size": 13}, "page_size must be between 1 and 12"),
    ],
)
def test_list_styles_rejects_unbounded_pages(kwargs, message):
    result = list_styles(**kwargs)

    assert result == {"status": "error", "error": message}


def test_load_brand_guideline_known_and_unknown():
    ok = load_brand_guideline("stripe")
    assert ok["status"] == "ok"
    assert "stripe" in ok["guideline"].lower() or "Stripe" in ok["guideline"]
    assert len(ok["guideline"]) > 1000

    bad = load_brand_guideline("not-a-brand")
    assert bad["status"] == "error"
    assert "Valid ids" in bad["error"]


def test_render_deck_validates_payload_before_calling_renderer(monkeypatch):
    # No renderer URL configured -> any valid payload must fail cleanly.
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)
    empty = render_deck_to_pptx(slides=[], deck_title="t", brand_id="stripe")
    assert empty["status"] == "error"

    too_many = render_deck_to_pptx(
        slides=[{"title": f"s{i}", "html": "<html></html>"} for i in range(11)],
        deck_title="t",
        brand_id="stripe",
    )
    assert too_many["status"] == "error"

    missing_html = render_deck_to_pptx(
        slides=[{"title": "s"}], deck_title="t", brand_id="stripe"
    )
    assert missing_html["status"] == "error"

    unconfigured = render_deck_to_pptx(
        slides=[{"title": "s", "html": "<html></html>"}],
        deck_title="t",
        brand_id="stripe",
    )
    assert unconfigured["status"] == "error"
    assert "SLIDEGEN_RENDERER_URL" in unconfigured["error"]


def test_inlined_plates_do_not_count_against_the_markup_budget(monkeypatch):
    # A template slide carries its clean plate as a data URI because a relative
    # src cannot survive the trip to the renderer. One plate is ~98KB, so
    # counting inlined bytes as authored markup rejects a six-slide deck that
    # is entirely well formed. Reaching the unset-URL error proves the size
    # gate let it through.
    plate = "data:image/png;base64," + "A" * 130_000
    deck = [
        {"title": f"s{i}", "html": f'<html><body><img src="{plate}"></body></html>'}
        for i in range(6)
    ]
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)

    result = render_deck_to_pptx(slides=deck, deck_title="t", brand_id="stripe")

    assert result["status"] == "error"
    assert "SLIDEGEN_RENDERER_URL" in result["error"]


def test_the_two_budgets_still_have_a_ceiling(monkeypatch):
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)

    markup = render_deck_to_pptx(
        slides=[{"title": "s", "html": "<p>x</p>" * 60_000}],
        deck_title="t",
        brand_id="stripe",
    )
    assert markup["status"] == "error"
    assert "HTML exceeds size limit" in markup["error"]

    payload = render_deck_to_pptx(
        slides=[
            {"title": f"s{i}", "html": f'<img src="data:image/png;base64,{"A" * 5_000_000}">'}
            for i in range(6)
        ],
        deck_title="t",
        brand_id="stripe",
    )
    assert payload["status"] == "error"
    assert "payload exceeds size limit" in payload["error"]


def test_preview_uses_flat_images_instead_of_nested_iframes():
    preview = _build_preview_html(
        [{"title": "Results < Q4"}], ["aGVsbG8="], "image/jpeg"
    )

    assert 'src="data:image/jpeg;base64,aGVsbG8="' in preview
    assert "Results &lt; Q4" in preview
    assert "<iframe" not in preview
    assert "<script" not in preview


def test_render_deck_returns_preview_image_tokens_without_inline_bytes(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_RENDERER_URL", "https://renderer.example")
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setenv("SLIDEGEN_RENDERER_API_KEY", "test-key")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "gs_uri": "gs://deck-bucket/decks/test.pptx",
                "https_url": "https://storage.example/deck.pptx",
                "slide_count": 2,
                "quality": {},
                "images_b64": ["Zmlyc3Q=", "c2Vjb25k"],
                "preview_mime": "image/png",
            }

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return Response()

    monkeypatch.setattr("app.tools.httpx.post", fake_post)
    tool_context = SimpleNamespace(session=SimpleNamespace(id="session-token"), state={})

    result = render_deck_to_pptx(
        slides=[
            {"title": "One", "html": "<html>one</html>"},
            {"title": "Two", "html": "<html>two</html>"},
        ],
        deck_title="Deck",
        brand_id="stripe",
        tool_context=tool_context,
    )

    assert result["preview_image_tokens"] == [
        "__SLIDE_PREVIEW_IMAGE_01__",
        "__SLIDE_PREVIEW_IMAGE_02__",
    ]
    assert result["preview_token"] == SLIDE_PREVIEW_TOKEN
    assert "Zmlyc3Q=" not in json.dumps(result)
    assert "c2Vjb25k" not in json.dumps(result)

    fragments = get_cached_fragments(_preview_key(tool_context))
    assert fragments["__SLIDE_PREVIEW_IMAGE_01__"] == "data:image/png;base64,Zmlyc3Q="
    assert fragments["__SLIDE_PREVIEW_IMAGE_02__"] == "data:image/png;base64,c2Vjb25k"
    assert SLIDE_PREVIEW_TOKEN in fragments
    assert captured["headers"]["X-API-Key"] == "test-key"


def test_render_deck_rejects_failed_quality_admission(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_RENDERER_URL", "https://renderer.example")
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "gs_uri": "gs://deck-bucket/decks/bad.pptx",
                "slide_count": 1,
                "quality": {
                    "editability_passed": False,
                    "editability_failing_slides": [0],
                    "overflow": [],
                    "coverage_gaps": [],
                    "fidelity_failures": [],
                    "small_text": [],
                },
            }

    monkeypatch.setattr("app.tools.httpx.post", lambda *args, **kwargs: Response())

    result = render_deck_to_pptx(
        slides=[{"title": "One", "html": "<html>one</html>"}],
        deck_title="Deck",
        brand_id="stripe",
    )

    assert result["status"] == "error"
    assert "quality admission" in result["error"].lower()


def test_template_revision_is_deterministic_and_source_derived():
    source = {
        "gs_uri": "gs://bucket/templates/acme.pptx",
        "generation": "12",
        "etag": "etag-12",
    }
    first = tools._template_revision(source)
    assert first == tools._template_revision(dict(source))
    assert first.startswith("tr1_")
    assert first != tools._template_revision({**source, "generation": "13"})


def test_the_browsing_gallery_does_not_hand_out_catalog_imagery(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_RENDERER_URL", "https://renderer.example")
    monkeypatch.delenv("SLIDEGEN_GCS_BUCKET", raising=False)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "ok": True,
                "templates": [
                    {
                        "name": "Acme Template.pptx",
                        "gs_uri": "gs://bucket/templates/acme.pptx",
                        "generation": "7",
                        "etag": "etag-7",
                    }
                ],
            }

    monkeypatch.setattr("app.tools.httpx.get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        tools,
        "_load_expression_catalog",
        lambda _name, **_kwargs: (
            [{"id": "ledger", "archetype": "comparison"}],
            [("ledger", b"png-bytes")],
        ),
    )
    ctx = SimpleNamespace(session=SimpleNamespace(id="template-gallery"))

    result = tools.list_templates(tool_context=ctx)

    template = result["templates"][0]
    assert template["revision"].startswith("tr1_")
    # Archetype names are descriptive and safe. The PNG beside each one is
    # authored on the template's own clean plate, so it carries that slide's
    # real artwork, and this surface lists every template in the bucket to a
    # user who named none of them.
    assert template["available_archetypes"] == ["comparison"]
    assert "preview_image_tokens" not in template
    assert "iframe" not in json.dumps(result).lower()


def test_expression_gallery_returns_aligned_flat_image_tokens(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_load_expression_catalog",
        lambda _name: (
            [
                {"id": "one", "world": "light", "archetype": "hero"},
                {"id": "two", "world": "dark", "archetype": "timeline"},
            ],
            [("one", b"one-png"), ("two", b"two-png")],
        ),
    )
    ctx = SimpleNamespace(session=SimpleNamespace(id="expression-gallery"))

    result = tools.list_expressions("Acme", tool_context=ctx)

    assert result["preview_expression_ids"] == ["one", "two"]
    assert len(result["preview_image_tokens"]) == 2
    assert [entry["preview_image_token"] for entry in result["expressions"]] == result[
        "preview_image_tokens"
    ]
    assert "gallery_token" not in result
    assert "iframe" not in json.dumps(result).lower()


def test_unknown_expression_id_fails_before_authoring(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_load_expression_catalog",
        lambda _name: ([{"id": "current", "archetype": "hero"}], []),
    )
    monkeypatch.setattr(
        "app.deck_run.run_turn",
        lambda **kwargs: pytest.fail("unknown expression reached authoring"),
    )

    result = tools._generate_deck_sync(
        topic="t",
        audience="a",
        goal="g",
        slide_count=1,
        brand_id="stripe",
        expression_ids="stale",
    )

    assert result["status"] == "error"
    assert "Unknown or stale" in result["error"]


# --------------------------------------------------------------- template admission

TEMPLATE_URI = "gs://deck-bucket/templates/everyday.pptx"
TEMPLATE_DISPLAY = "Copy of PLEASE MAKE A COPY_Everyday AU main template feb 2026"


def _catch_enqueued_builds(monkeypatch) -> list[str]:
    """Record the slug each enqueued precompute turn would publish under."""
    slugs: list[str] = []
    monkeypatch.setenv("SLIDEGEN_AUTO_PRECOMPUTE", "1")
    monkeypatch.setattr(tools, "_catalog_exists_in_gcs", lambda _slug: False)
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", set())
    monkeypatch.setattr(
        tools._PIPELINE_POOL,
        "submit",
        lambda _fn, _uri, slug, _display: slugs.append(slug),
    )
    return slugs


def test_a_template_builds_under_the_slug_the_deck_lane_reads(monkeypatch):
    from app.deck_run import slug_for

    slugs = _catch_enqueued_builds(monkeypatch)

    assert tools.maybe_build_catalog(TEMPLATE_URI, TEMPLATE_DISPLAY) is True

    # The lane resolves a prepared bundle with slug_for. Publishing under any
    # other rule leaves the bundle somewhere nothing reads and the template
    # unadmitted forever.
    assert slugs == [slug_for(TEMPLATE_DISPLAY)]


def test_a_display_name_and_its_slug_are_one_template(monkeypatch):
    from app.deck_run import slug_for

    slugs = _catch_enqueued_builds(monkeypatch)

    # Ingest passes the resolved slug, the unadmitted-template retry passes the
    # display name it came from. Both have to mean the same build.
    tools.maybe_build_catalog(TEMPLATE_URI, TEMPLATE_DISPLAY)
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", set())
    tools.maybe_build_catalog(TEMPLATE_URI, slug_for(TEMPLATE_DISPLAY))

    assert len(slugs) == 2
    assert len(set(slugs)) == 1


def test_retrying_during_a_build_does_not_queue_a_second_one(monkeypatch):
    slugs = _catch_enqueued_builds(monkeypatch)

    assert tools.maybe_build_catalog(TEMPLATE_URI, TEMPLATE_DISPLAY) is True
    assert tools.maybe_build_catalog(TEMPLATE_URI, TEMPLATE_DISPLAY) is True

    assert slugs == slugs[:1]


def test_a_finished_build_releases_the_template_for_a_later_rebuild(monkeypatch):
    from google.cloud import storage

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("no storage in tests")

    monkeypatch.setattr(storage, "Client", unavailable)
    in_flight = {"everyday-au"}
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", in_flight)

    tools._build_catalog(TEMPLATE_URI, "everyday-au", TEMPLATE_DISPLAY)

    # A build that dies must not leave the template permanently unbuildable.
    assert in_flight == set()


def test_a_missing_deliverable_is_named_rather_than_crashing_the_collector(
    monkeypatch, tmp_path
):
    from app import template_policy

    reasons: list[str] = []
    monkeypatch.setattr(tools, "_CATALOG_BUILD_FAILURES", {})
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", set())

    def fake_manifest(*, source, artifacts):
        # The one place that rules on admissibility, reached with whatever the
        # turn actually produced instead of being pre-empted by a raw read.
        reasons.append(",".join(sorted(artifacts)))
        raise template_policy.TemplatePolicyError(
            "prepared bundle is missing: brand-contract.json"
        )

    _stage_catalog_turn(monkeypatch, tmp_path, manifest_builder=fake_manifest)

    tools._build_catalog(TEMPLATE_URI, "everyday-au", TEMPLATE_DISPLAY)

    assert reasons and "brand-contract.json" not in reasons[0]
    assert "template.pptx" in reasons[0]
    assert (
        "brand-contract.json" in tools._CATALOG_BUILD_FAILURES["everyday-au"]
    )


def test_a_repeatedly_failing_template_stops_promising_a_deck(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_CATALOG_BUILD_FAILURES",
        {"everyday-au": "prepared bundle is missing: brand-contract.json"},
    )

    assert tools.catalog_build_failure("Everyday AU") == (
        "prepared bundle is missing: brand-contract.json"
    )
    assert tools.catalog_build_failure("Some Other Template") is None


def test_a_successful_build_clears_an_earlier_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "_CATALOG_BUILD_FAILURES", {"everyday-au": "old"})
    monkeypatch.setattr(tools, "_CATALOG_BUILDS_IN_FLIGHT", set())

    _stage_catalog_turn(
        monkeypatch, tmp_path, manifest_builder=lambda **_kw: _FakeManifest()
    )

    tools._build_catalog(TEMPLATE_URI, "everyday-au", TEMPLATE_DISPLAY)

    assert "everyday-au" not in tools._CATALOG_BUILD_FAILURES


class _FakeManifest:
    bundle_id = "abcdef0123456789"

    def to_json(self) -> str:
        return "{}"


def test_gs_template_inventory_failure_never_degrades_to_style_transfer(monkeypatch):
    import httpx

    monkeypatch.setenv("SLIDEGEN_RENDERER_URL", "https://renderer.example")
    monkeypatch.setattr(tools, "_renderer_auth_headers", lambda *_: {})
    monkeypatch.setattr("app.reference.ingest", lambda **_: ({"name": "Template"}, None))
    monkeypatch.setattr(tools.httpx, "get", lambda *_args, **_kwargs: httpx.Response(
        200, json={"ok": False, "error": "invalid source"}, request=httpx.Request("GET", "https://renderer.example/template-layouts")))
    result = tools._use_reference_deck_sync(source_url="gs://bucket/template.pptx")
    assert result["status"] == "error"
    assert "No style-transfer fallback" in result["error"]


class _FakeStorage:
    """Enough of the GCS client for _build_catalog to reach its validation."""

    generation = 1
    etag = "etag"

    def bucket(self, _name):
        return self

    def blob(self, _path):
        return self

    def reload(self):
        return None

    def download_as_bytes(self, **_kwargs) -> bytes:
        return b"pptx-bytes"

    def upload_from_string(self, *_args, **_kwargs):
        return None

    def upload_from_filename(self, *_args, **_kwargs):
        return None


def _stage_catalog_turn(monkeypatch, tmp_path, *, manifest_builder):
    """A precompute turn that writes its catalog but no brand contract."""
    import tempfile

    from google.cloud import storage

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(workdir))
    monkeypatch.delenv("SLIDEGEN_RENDERER_URL", raising=False)
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setattr(storage, "Client", lambda: _FakeStorage())
    monkeypatch.setattr(tools, "_stage_template_source", lambda *_args: {})
    monkeypatch.setattr(
        "app.template_policy.build_prepared_manifest", manifest_builder
    )

    def fake_turn(**_kwargs):
        (workdir / "catalog.json").write_text("[]", encoding="utf-8")
        return ""

    monkeypatch.setattr("app.agy_authoring.generate", fake_turn)


# --- template gallery admission


def _manifest_payload(gs_uri: str, generation: str, etag: str) -> dict:
    """The smallest manifest PreparedBundleManifest.from_dict will accept."""
    digest = "a" * 64
    return {
        "schema_version": 1,
        "bundle_id": "b" * 64,
        "source": {
            "gs_uri": gs_uri,
            "generation": generation,
            "etag": etag,
            "sha256": digest,
        },
        "artifacts": [
            {"path": "base/title.html", "size_bytes": 10, "sha256": digest}
        ],
        "slide_types": [
            {
                "id": "title",
                "archetype": "opening",
                "baseline_path": "base/title.html",
                "preview_path": None,
                "seams": [
                    {"id": "headline", "kind": "content", "allowed_tags": ["em"]}
                ],
            }
        ],
    }


class _GalleryBucket:
    """A bucket holding prepared bundles under an exact set of prefixes."""

    def __init__(self, bundles: dict[str, bytes]):
        self._bundles = bundles
        self.probed: list[str] = []

    def blob(self, path: str):
        self.probed.append(path)
        payload = self._bundles.get(path)
        return SimpleNamespace(
            exists=lambda: payload is not None,
            download_as_bytes=lambda: payload,
        )


def _gallery_item(name: str = "Copy of Acme Template.pptx") -> dict:
    return {
        "name": name,
        "gs_uri": f"gs://deck-bucket/templates/{name}",
        "generation": "7",
        "etag": "etag-7",
    }


def _stage_gallery(monkeypatch, bundles: dict[str, bytes]) -> _GalleryBucket:
    from google.cloud import storage

    bucket = _GalleryBucket(bundles)
    monkeypatch.setenv("SLIDEGEN_GCS_BUCKET", "deck-bucket")
    monkeypatch.setattr(
        storage, "Client", lambda: SimpleNamespace(bucket=lambda _name: bucket)
    )
    monkeypatch.setattr(tools, "_load_expression_catalog", lambda _name, **_kwargs: None)
    return bucket


def test_the_gallery_probes_the_prefix_the_bundle_was_published_under(monkeypatch):
    item = _gallery_item()
    payload = json.dumps(
        _manifest_payload(item["gs_uri"], "7", "etag-7")
    ).encode()
    bucket = _stage_gallery(
        monkeypatch, {"templates/acme/prepared/manifest.json": payload}
    )

    result = tools._template_gallery_metadata(item, None)

    # Slugging "Copy of Acme Template.pptx" whole yields "acme-pptx", a prefix
    # nothing publishes to, so every template reports unadmitted forever.
    from app.template_versions import TemplateVersion
    version = TemplateVersion.from_uri(item["gs_uri"], item["generation"])
    assert bucket.probed == [version.prefix + "ready.json", "templates/acme/prepared/manifest.json"]
    assert [entry["id"] for entry in result["admitted_slide_types"]] == ["title"]
    assert result["admitted"] is True


def test_a_template_with_no_bundle_is_offered_as_unadmitted(monkeypatch):
    _stage_gallery(monkeypatch, {})

    result = tools._template_gallery_metadata(_gallery_item(), None)

    assert result["admitted_slide_types"] == []
    assert result["admitted"] is False


def test_a_bundle_built_from_a_superseded_upload_does_not_admit(monkeypatch):
    item = _gallery_item()
    stale = json.dumps(
        _manifest_payload(item["gs_uri"], "6", "etag-6")
    ).encode()
    _stage_gallery(monkeypatch, {"templates/acme/prepared/manifest.json": stale})

    result = tools._template_gallery_metadata(item, None)

    assert result["admitted"] is False


def test_a_declared_specimen_becomes_a_flat_session_image_token(monkeypatch):
    item = _gallery_item()
    payload = _manifest_payload(item["gs_uri"], "7", "etag-7")
    payload["artifacts"].append(
        {"path": "specimens/title.png", "size_bytes": 10, "sha256": "a" * 64}
    )
    payload["slide_types"][0]["preview_path"] = "specimens/title.png"
    _stage_gallery(
        monkeypatch,
        {
            "templates/acme/prepared/manifest.json": json.dumps(payload).encode(),
            "templates/acme/prepared/specimens/title.png": b"png-bytes",
        },
    )
    ctx = SimpleNamespace(session=SimpleNamespace(id="declared-gallery"))

    result = tools._template_gallery_metadata(item, ctx)

    assert len(result["preview_image_tokens"]) == 1
    token = result["preview_image_tokens"][0]
    assert token.startswith("__TEMPLATE_PREVIEW_IMAGE_")
    assert tools.get_cached_fragments("session:declared-gallery")[token].startswith(
        "data:image/png;base64,"
    )
    assert "iframe" not in json.dumps(result).lower()


def test_a_bundle_declaring_no_preview_never_falls_back_to_a_source_slide(monkeypatch):
    # base/*.png is one raw page render per slide of the customer's own deck,
    # and the bundle's brand contract can protect any of them. Enumerating them
    # for a thumbnail put slides 01-03 on the intake form knowing none of that.
    item = _gallery_item()
    payload = _manifest_payload(item["gs_uri"], "7", "etag-7")
    payload["artifacts"] += [
        {"path": f"base/{page}.png", "size_bytes": 10, "sha256": "a" * 64}
        for page in ("01", "02", "03")
    ]
    bucket = _stage_gallery(
        monkeypatch,
        {
            "templates/acme/prepared/manifest.json": json.dumps(payload).encode(),
            "templates/acme/prepared/base/01.png": b"page-01",
            "templates/acme/prepared/base/02.png": b"page-02",
            "templates/acme/prepared/base/03.png": b"page-03",
        },
    )
    ctx = SimpleNamespace(session=SimpleNamespace(id="protected-gallery"))

    result = tools._template_gallery_metadata(item, ctx)

    assert payload["slide_types"][0]["preview_path"] is None
    assert result["admitted"] is True
    assert "preview_image_tokens" not in result
    assert [path for path in bucket.probed if path.endswith(".png")] == []


def test_the_gallery_carries_a_readable_label_alongside_the_object_name(monkeypatch):
    # name stays the storage object, because catalog lookup and the deck lane
    # key off it. display_name is the only thing a user should ever read.
    _stage_gallery(monkeypatch, {})
    item = _gallery_item("Copy of PLEASE MAKE A COPY_Everyday AU main template feb 2026.pptx")

    result = tools._template_gallery_metadata(item, None)

    assert result["display_name"] == "Everyday AU main feb 2026"
    assert result["name"].endswith(".pptx")
