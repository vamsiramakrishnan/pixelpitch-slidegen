"""Tier-3 LLM adjudicator.

Sends a single batched call per slide, covering all units that tier 2 deferred.
Provider-agnostic: works with Gemini (AI Studio or Vertex) and Claude (Anthropic
or Vertex). See `slidify.classifier.llm`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from slidify.classifier.llm import LLMProvider, auto_select_backend, build_provider
from slidify.classifier.prompts import SYSTEM_PROMPT, build_user_prompt
from slidify.models import Decision, DecisionKind, VisualUnit

log = structlog.get_logger(__name__)


@dataclass
class Tier3Stats:
    n_calls: int = 0
    n_units: int = 0
    cost_usd: float = 0.0
    backend: str = ""
    model: str = ""
    by_backend: dict[str, int] = field(default_factory=dict)


def _summarize_css(unit: VisualUnit) -> str:
    parts: list[str] = []
    elems = unit.all_elements()
    if not elems:
        return ""
    a = elems[0]
    parts.append(f"tag={a.tag}")
    if a.background_color and a.background_color != "rgba(0, 0, 0, 0)":
        parts.append(f"bg={a.background_color}")
    if a.background_image and a.background_image != "none":
        parts.append(f"bg-image={a.background_image[:80]}")
    if a.box_shadow and a.box_shadow != "none":
        parts.append(f"shadow={a.box_shadow[:80]}")
    if a.transform and a.transform != "none":
        parts.append(f"transform={a.transform[:60]}")
    if a.filter and a.filter != "none":
        parts.append(f"filter={a.filter[:60]}")
    if a.clip_path and a.clip_path != "none":
        parts.append(f"clip-path={a.clip_path[:60]}")
    if a.has_before:
        parts.append(f"::before={a.before_content!r}"[:60])
    if a.has_after:
        parts.append(f"::after={a.after_content!r}"[:60])
    text_count = sum(1 for e in elems if e.text and e.text.strip())
    parts.append(f"n_elems={len(elems)}, n_text={text_count}")
    return "; ".join(parts)


def _html_excerpt(unit: VisualUnit) -> str:
    elems = unit.all_elements()
    if not elems:
        return ""
    a = elems[0]
    cls = (a.cls or "")[:120]
    text = ""
    for e in elems[:5]:
        if e.text and e.text.strip():
            text = e.text.strip()[:80]
            break
    return f"<{a.tag.lower()} class=\"{cls}\">{text}</{a.tag.lower()}>"


async def classify_tier3(
    deferred: list[VisualUnit],
    ground_truth_png: bytes,
    *,
    provider: LLMProvider | None = None,
    backend: str | None = None,
    model: str | None = None,
    timeout_s: float = 60.0,
) -> tuple[dict[str, Decision], Tier3Stats]:
    """Adjudicate deferred units via a vision-capable LLM.

    Pass either a constructed `provider` or a `backend` name and we'll build it.
    Falls back to Raster decisions if no provider is available or the call fails.
    """
    stats = Tier3Stats(n_units=len(deferred))
    if not deferred:
        return {}, stats

    if provider is None:
        chosen_backend = backend or auto_select_backend()
        if chosen_backend is None:
            log.warning(
                "tier3.no_provider",
                note="no LLM credentials in env; falling back to raster",
            )
            return _fallback_decisions(deferred, "no_provider"), stats
        try:
            provider = build_provider(
                chosen_backend, model=model, timeout_s=timeout_s
            )
        except Exception as e:
            log.warning("tier3.provider_init_failed", backend=chosen_backend, error=str(e))
            return _fallback_decisions(deferred, f"provider_init: {e}"), stats

    stats.backend = provider.name
    stats.model = provider.model

    regions = [
        {
            "id": u.id,
            "html": _html_excerpt(u),
            "css_summary": _summarize_css(u),
        }
        for u in deferred
    ]
    user_text = build_user_prompt(regions)

    try:
        resp = await provider.classify(SYSTEM_PROMPT, user_text, [ground_truth_png])
    except Exception as e:
        log.warning("tier3.api_error", backend=provider.name, error=str(e))
        return _fallback_decisions(deferred, f"api_error: {e}"), stats

    stats.n_calls = 1
    stats.cost_usd = resp.cost_usd
    stats.by_backend[provider.name] = 1

    decisions = _parse_response(resp.text, deferred)
    log.info(
        "tier3.classified",
        backend=provider.name,
        model=provider.model,
        n_units=len(deferred),
        cost_usd=resp.cost_usd,
        in_tok=resp.input_tokens,
        out_tok=resp.output_tokens,
    )
    return decisions, stats


def _parse_response(text: str, deferred: list[VisualUnit]) -> dict[str, Decision]:
    """Extract JSON from possibly-wrapped LLM output."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        log.warning("tier3.bad_json", text=text[:200])
        return _fallback_decisions(deferred, "bad_json")

    out: dict[str, Decision] = {}
    for u in deferred:
        entry = parsed.get(u.id) if isinstance(parsed, dict) else None
        if not isinstance(entry, dict):
            out[u.id] = Decision(
                kind=DecisionKind.Raster,
                confidence=0.5,
                reason="missing_in_llm_response",
                source_tier="tier3",
            )
            continue
        decision_str = str(entry.get("decision", "raster")).lower()
        try:
            confidence = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        reason = str(entry.get("reason", ""))
        if decision_str == "native":
            kind = _native_kind_for(u)
        else:
            kind = DecisionKind.Raster
        out[u.id] = Decision(
            kind=kind,
            confidence=max(0.0, min(1.0, confidence)),
            reason=reason or decision_str,
            source_tier="tier3",
        )
    return out


def _native_kind_for(unit: VisualUnit) -> DecisionKind:
    elems = unit.all_elements()
    has_text = any(e.text and e.text.strip() for e in elems)
    has_image = any(e.is_img for e in elems)
    if has_image and not has_text:
        return DecisionKind.NativePicture
    if has_text:
        return DecisionKind.NativeText
    return DecisionKind.NativeShape


def _fallback_decisions(units: list[VisualUnit], reason: str) -> dict[str, Decision]:
    return {
        u.id: Decision(
            kind=DecisionKind.Raster,
            confidence=0.5,
            reason=f"tier3_fallback: {reason}",
            source_tier="tier3",
        )
        for u in units
    }
