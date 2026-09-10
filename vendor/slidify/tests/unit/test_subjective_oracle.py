"""Tests for slidify.subjective_oracle.

These tests never make real API calls — the LLMProvider is mocked. They cover:
  - Composite weighting math (must match documented weights)
  - Graceful no-provider fallback (zero scores, no exceptions)
  - JSON parse robustness (malformed model output → safe defaults)
  - score_slide / score_corpus behavior with a mocked provider
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from slidify.classifier.llm import LLMProvider, LLMResponse
from slidify.subjective_oracle import (
    DEFAULT_MAX_SLIDES,
    WEIGHT_INTENT,
    WEIGHT_LAYOUT,
    WEIGHT_TYPE,
    WEIGHT_VISUAL,
    SubjectiveOracle,
    SubjectiveScore,
    parse_score,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip LLM credentials so auto_select_backend() returns None by default."""
    for var in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "SLIDIFY_PREFER_VERTEX_GEMINI",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ----- Mock provider -----------------------------------------------------------


@dataclass
class _StubProvider(LLMProvider):
    """Minimal LLMProvider that returns a queued response and records calls."""

    name: str = "stub"

    def __init__(self, response_text: str = "{}", *, raise_exc: Exception | None = None):
        # bypass LLMProvider.__init__'s required model arg sensibly
        self.model = "stub-model"
        self.timeout_s = 1.0
        self._response_text = response_text
        self._raise_exc = raise_exc
        self.calls: list[tuple[str, str, list[bytes]]] = []

    async def classify(
        self, system: str, user_text: str, images: list[bytes]
    ) -> LLMResponse:
        self.calls.append((system, user_text, list(images)))
        if self._raise_exc is not None:
            raise self._raise_exc
        return LLMResponse(
            text=self._response_text,
            input_tokens=10,
            output_tokens=20,
            cost_usd=0.001,
        )


# ----- Composite weighting -----------------------------------------------------


def test_weights_sum_to_one():
    total = WEIGHT_INTENT + WEIGHT_TYPE + WEIGHT_LAYOUT + WEIGHT_VISUAL
    assert total == pytest.approx(1.0)


def test_composite_zero_for_zero_scores():
    s = SubjectiveScore(0.0, 0.0, 0.0, 0.0)
    assert s.composite == 0.0


def test_composite_one_for_perfect_scores():
    s = SubjectiveScore(1.0, 1.0, 1.0, 1.0)
    assert s.composite == pytest.approx(1.0)


def test_composite_uses_documented_weights():
    s = SubjectiveScore(
        visual_quality=0.2,
        intent_fidelity=0.4,
        type_quality=0.6,
        layout_quality=0.8,
    )
    expected = (
        WEIGHT_VISUAL * 0.2
        + WEIGHT_INTENT * 0.4
        + WEIGHT_TYPE * 0.6
        + WEIGHT_LAYOUT * 0.8
    )
    assert s.composite == pytest.approx(expected)


def test_composite_intent_dominates_visual():
    """Intent fidelity should outweigh visual polish at equal magnitude."""
    high_intent_low_visual = SubjectiveScore(0.0, 1.0, 0.0, 0.0).composite
    high_visual_low_intent = SubjectiveScore(1.0, 0.0, 0.0, 0.0).composite
    assert high_intent_low_visual > high_visual_low_intent


def test_zero_factory():
    s = SubjectiveScore.zero(notes="hello")
    assert s.visual_quality == 0.0
    assert s.intent_fidelity == 0.0
    assert s.type_quality == 0.0
    assert s.layout_quality == 0.0
    assert s.notes == "hello"
    assert s.composite == 0.0


# ----- JSON parse robustness ---------------------------------------------------


def test_parse_score_valid_json():
    raw = (
        '{"visual_quality": 0.85, "intent_fidelity": 0.78, '
        '"type_quality": 0.82, "layout_quality": 0.90, '
        '"notes": "Strong overall."}'
    )
    s = parse_score(raw)
    assert s.visual_quality == 0.85
    assert s.intent_fidelity == 0.78
    assert s.type_quality == 0.82
    assert s.layout_quality == 0.90
    assert s.notes == "Strong overall."


def test_parse_score_strips_code_fence():
    raw = '```json\n{"visual_quality": 0.5, "intent_fidelity": 0.5, "type_quality": 0.5, "layout_quality": 0.5, "notes": "ok"}\n```'
    s = parse_score(raw)
    assert s.visual_quality == 0.5
    assert s.notes == "ok"


def test_parse_score_strips_bare_fence():
    raw = '```\n{"visual_quality": 0.5, "intent_fidelity": 0.5, "type_quality": 0.5, "layout_quality": 0.5}\n```'
    s = parse_score(raw)
    assert s.visual_quality == 0.5


def test_parse_score_recovers_from_surrounding_prose():
    raw = 'Sure! Here is the JSON: {"visual_quality": 0.7, "intent_fidelity": 0.7, "type_quality": 0.7, "layout_quality": 0.7, "notes": "fine"} Hope this helps.'
    s = parse_score(raw)
    assert s.visual_quality == 0.7
    assert s.notes == "fine"


def test_parse_score_malformed_returns_zero():
    s = parse_score("this is not JSON at all")
    assert s.composite == 0.0
    assert "bad_json" in s.notes


def test_parse_score_empty_returns_zero():
    s = parse_score("")
    assert s.composite == 0.0
    assert s.notes == "empty_response"


def test_parse_score_non_object_returns_zero():
    s = parse_score("[1, 2, 3]")
    assert s.composite == 0.0
    assert "not_object" in s.notes


def test_parse_score_missing_keys_default_to_zero():
    s = parse_score('{"visual_quality": 0.9}')
    assert s.visual_quality == 0.9
    assert s.intent_fidelity == 0.0
    assert s.type_quality == 0.0
    assert s.layout_quality == 0.0


def test_parse_score_clamps_out_of_range():
    raw = '{"visual_quality": 1.5, "intent_fidelity": -0.3, "type_quality": 999, "layout_quality": 0.5}'
    s = parse_score(raw)
    assert s.visual_quality == 1.0
    assert s.intent_fidelity == 0.0
    assert s.type_quality == 1.0
    assert s.layout_quality == 0.5


def test_parse_score_non_numeric_values_become_zero():
    raw = '{"visual_quality": "high", "intent_fidelity": null, "type_quality": 0.5, "layout_quality": 0.5}'
    s = parse_score(raw)
    assert s.visual_quality == 0.0
    assert s.intent_fidelity == 0.0
    assert s.type_quality == 0.5


# ----- SubjectiveOracle behavior -----------------------------------------------


@pytest.mark.asyncio
async def test_score_slide_no_provider_returns_zero():
    """No env credentials, no injected provider → graceful zero score."""
    oracle = SubjectiveOracle()
    score = await oracle.score_slide(b"src", b"cand")
    assert isinstance(score, SubjectiveScore)
    assert score.composite == 0.0
    assert "no_provider" in score.notes


@pytest.mark.asyncio
async def test_score_slide_with_mock_provider():
    raw = (
        '{"visual_quality": 0.8, "intent_fidelity": 0.9, '
        '"type_quality": 0.7, "layout_quality": 0.85, '
        '"notes": "Excellent fidelity to design intent."}'
    )
    provider = _StubProvider(response_text=raw)
    oracle = SubjectiveOracle(provider=provider)

    score = await oracle.score_slide(b"src-png-bytes", b"cand-png-bytes")

    assert score.intent_fidelity == 0.9
    assert score.visual_quality == 0.8
    assert "design intent" in score.notes
    # Provider was called with both images, in source-then-candidate order.
    assert len(provider.calls) == 1
    _system, _user, images = provider.calls[0]
    assert images == [b"src-png-bytes", b"cand-png-bytes"]


@pytest.mark.asyncio
async def test_score_slide_passes_html_summary_into_user_text():
    provider = _StubProvider(response_text='{"visual_quality": 0.5, "intent_fidelity": 0.5, "type_quality": 0.5, "layout_quality": 0.5}')
    oracle = SubjectiveOracle(provider=provider)
    await oracle.score_slide(b"a", b"b", source_html_summary="<h1>Title</h1>")
    _system, user, _images = provider.calls[0]
    assert "<h1>Title</h1>" in user


@pytest.mark.asyncio
async def test_score_slide_api_error_returns_zero():
    provider = _StubProvider(raise_exc=RuntimeError("boom"))
    oracle = SubjectiveOracle(provider=provider)
    score = await oracle.score_slide(b"a", b"b")
    assert score.composite == 0.0
    assert "api_error" in score.notes
    assert "boom" in score.notes


@pytest.mark.asyncio
async def test_score_slide_malformed_response_returns_zero():
    provider = _StubProvider(response_text="not json")
    oracle = SubjectiveOracle(provider=provider)
    score = await oracle.score_slide(b"a", b"b")
    assert score.composite == 0.0


@pytest.mark.asyncio
async def test_score_corpus_empty():
    oracle = SubjectiveOracle(provider=_StubProvider())
    assert await oracle.score_corpus([]) == []


@pytest.mark.asyncio
async def test_score_corpus_returns_one_score_per_slide():
    raw = '{"visual_quality": 0.5, "intent_fidelity": 0.5, "type_quality": 0.5, "layout_quality": 0.5, "notes": "ok"}'
    provider = _StubProvider(response_text=raw)
    oracle = SubjectiveOracle(provider=provider)
    pairs = [(b"s0", b"c0"), (b"s1", b"c1"), (b"s2", b"c2")]
    scores = await oracle.score_corpus(pairs)
    assert len(scores) == 3
    assert all(s.composite == pytest.approx(0.5) for s in scores)
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_score_corpus_no_provider_returns_zero_list():
    """No credentials → list of zero scores, never raises."""
    oracle = SubjectiveOracle()
    pairs = [(b"s0", b"c0"), (b"s1", b"c1")]
    scores = await oracle.score_corpus(pairs)
    assert len(scores) == 2
    assert all(s.composite == 0.0 for s in scores)
    assert all("no_provider" in s.notes for s in scores)


@pytest.mark.asyncio
async def test_score_corpus_respects_max_slides():
    raw = '{"visual_quality": 0.5, "intent_fidelity": 0.5, "type_quality": 0.5, "layout_quality": 0.5}'
    provider = _StubProvider(response_text=raw)
    oracle = SubjectiveOracle(provider=provider, max_slides=2)
    pairs = [(b"s", b"c")] * 5
    scores = await oracle.score_corpus(pairs)

    assert len(scores) == 5
    # First 2 hit the LLM, remaining 3 are skipped.
    assert len(provider.calls) == 2
    assert scores[0].composite == pytest.approx(0.5)
    assert scores[1].composite == pytest.approx(0.5)
    for skipped in scores[2:]:
        assert skipped.composite == 0.0
        assert "skipped_max_slides" in skipped.notes


def test_default_max_slides_is_48():
    """Brief specifies 48 as the default cost cap."""
    assert DEFAULT_MAX_SLIDES == 48
