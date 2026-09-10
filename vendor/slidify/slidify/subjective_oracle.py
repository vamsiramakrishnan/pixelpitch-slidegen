"""Subjective-quality oracle.

Runs alongside the existing SSIM/OCR `FidelityOracle`. The pixel-fidelity oracle
penalises improvements we make on purpose — promoted decorations, OKLCH
gradients, font-genre restoration via embedding — because the rendered slide
diverges from the source at the pixel level.

This oracle asks a vision LLM the question we actually care about:

    "Does the rendered slide look professionally designed and faithful to the
    source's design *intent*?"

It is purely additive: nothing in `slidify.oracle` changes. Both oracles can
run side by side and produce independent reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from slidify.classifier.llm import LLMProvider, auto_select_backend, build_provider

log = structlog.get_logger(__name__)


# Composite weights. Intent fidelity matters most — it's the reason this oracle
# exists at all (SSIM already covers pixel fidelity). Type and layout get equal
# weight because typography errors are more visually offensive than minor color
# drift, while raw "polish" gets the smallest weight since a slide can look
# polished while completely missing the source's intent.
WEIGHT_INTENT = 0.30
WEIGHT_TYPE = 0.25
WEIGHT_LAYOUT = 0.25
WEIGHT_VISUAL = 0.20

# Practical caps. Vision LLMs charge per image; one slide-pair is two images.
DEFAULT_MAX_SLIDES = 48
DEFAULT_BATCH_SIZE = 1  # one slide-pair per call — keeps prompts focused

SYSTEM_PROMPT = (
    "You are a senior visual designer judging two slide renders.\n"
    "IMAGE A is the source HTML rendered in Chromium (the design 'intent').\n"
    "IMAGE B is the same slide compiled to PPTX and rendered in LibreOffice.\n"
    "\n"
    "Score IMAGE B on four 0-1 axes (1.0 = perfect, 0.0 = terrible):\n"
    "  - visual_quality:  how professionally designed does B look on its own,\n"
    "                     regardless of A?\n"
    "  - intent_fidelity: how well does B convey what A's design *intended*?\n"
    "                     A faithful interpretation that diverges in pixels but\n"
    "                     preserves intent should score high here.\n"
    "  - type_quality:    is the typography (face / weight / size / hierarchy)\n"
    "                     right? Genre-correct font substitutions are fine.\n"
    "  - layout_quality:  is spacing, alignment, and positioning preserved?\n"
    "\n"
    "Return ONLY a JSON object with these exact keys and a one-sentence\n"
    "'notes' field, e.g.:\n"
    '{"visual_quality": 0.85, "intent_fidelity": 0.78, '
    '"type_quality": 0.82, "layout_quality": 0.90, '
    '"notes": "Strong overall; the gradient text is muted vs source."}\n'
    "Do not include any commentary outside the JSON."
)


@dataclass
class SubjectiveScore:
    """Per-slide subjective scoring across four axes plus a one-line critique.

    All four axis scores are clamped to [0, 1]. The `composite` property is a
    fixed weighted combination — see module-level WEIGHT_* constants.
    """

    visual_quality: float
    intent_fidelity: float
    type_quality: float
    layout_quality: float
    notes: str = ""

    @property
    def composite(self) -> float:
        """Weighted composite score in [0, 1].

        Weights (sum to 1.0):
          - intent_fidelity: 0.30  (the metric SSIM cannot give us)
          - type_quality:    0.25
          - layout_quality:  0.25
          - visual_quality:  0.20
        """
        return (
            WEIGHT_INTENT * self.intent_fidelity
            + WEIGHT_TYPE * self.type_quality
            + WEIGHT_LAYOUT * self.layout_quality
            + WEIGHT_VISUAL * self.visual_quality
        )

    @classmethod
    def zero(cls, notes: str = "") -> SubjectiveScore:
        return cls(0.0, 0.0, 0.0, 0.0, notes)


def _clamp01(x: object, default: float = 0.0) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        # Drop the opening ``` (and any language tag) up to the first newline.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    return text


def parse_score(raw_text: str) -> SubjectiveScore:
    """Parse an LLM response into a SubjectiveScore.

    Robust to:
      - Markdown code fences (```json ... ```)
      - Surrounding prose
      - Missing keys (default 0.0)
      - Non-numeric values (clamped to 0.0)
      - Out-of-range values (clamped to [0, 1])
    """
    text = _strip_code_fence(raw_text)
    if not text:
        return SubjectiveScore.zero(notes="empty_response")

    parsed: object
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to salvage the first {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                log.warning("subjective.bad_json", text=text[:200])
                return SubjectiveScore.zero(notes="bad_json")
        else:
            log.warning("subjective.bad_json", text=text[:200])
            return SubjectiveScore.zero(notes="bad_json")

    if not isinstance(parsed, dict):
        return SubjectiveScore.zero(notes="not_object")

    notes_val = parsed.get("notes", "")
    notes = str(notes_val) if notes_val is not None else ""
    return SubjectiveScore(
        visual_quality=_clamp01(parsed.get("visual_quality")),
        intent_fidelity=_clamp01(parsed.get("intent_fidelity")),
        type_quality=_clamp01(parsed.get("type_quality")),
        layout_quality=_clamp01(parsed.get("layout_quality")),
        notes=notes,
    )


class SubjectiveOracle:
    """Vision-LLM-based subjective scoring of rendered slides.

    Reuses the same `LLMProvider` abstraction as tier-3 classification, so any
    backend that handles images (Gemini AI Studio / Vertex, Anthropic, Claude on
    Vertex) works out of the box.

    If no provider is configured AND no provider is passed, the oracle returns
    zero-score reports rather than raising — the existing FidelityOracle keeps
    running and the bench never fails for lack of LLM credentials.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        backend: str | None = None,
        model: str | None = None,
        timeout_s: float = 60.0,
        max_slides: int = DEFAULT_MAX_SLIDES,
    ) -> None:
        self._provider: LLMProvider | None = provider
        self._backend_arg = backend
        self._model_arg = model
        self._timeout_s = timeout_s
        self.max_slides = max_slides
        self._init_attempted = provider is not None

    def _ensure_provider(self) -> LLMProvider | None:
        """Lazily build a provider from env if one wasn't injected."""
        if self._provider is not None:
            return self._provider
        if self._init_attempted:
            return None
        self._init_attempted = True
        chosen = self._backend_arg or auto_select_backend()
        if chosen is None:
            log.info(
                "subjective.no_provider",
                note="no LLM credentials in env; subjective scoring disabled",
            )
            return None
        try:
            self._provider = build_provider(
                chosen, model=self._model_arg, timeout_s=self._timeout_s
            )
        except Exception as e:  # pragma: no cover — exercised in integration
            log.warning("subjective.provider_init_failed", backend=chosen, error=str(e))
            return None
        return self._provider

    async def score_slide(
        self,
        source_png: bytes,
        candidate_png: bytes,
        source_html_summary: str | None = None,
    ) -> SubjectiveScore:
        """Score one (source, candidate) pair.

        Returns SubjectiveScore.zero(...) if no provider is available or the
        API call fails. Never raises.
        """
        provider = self._ensure_provider()
        if provider is None:
            return SubjectiveScore.zero(notes="no_provider")

        user_text = "Compare IMAGE A (source intent) to IMAGE B (PPTX render)."
        if source_html_summary:
            user_text += (
                "\n\nFor reference, the source HTML can be summarised as:\n"
                f"{source_html_summary[:500]}"
            )
        user_text += "\n\nReturn the JSON object now."

        try:
            resp = await provider.classify(
                SYSTEM_PROMPT,
                user_text,
                [source_png, candidate_png],
            )
        except Exception as e:
            log.warning("subjective.api_error", backend=provider.name, error=str(e))
            return SubjectiveScore.zero(notes=f"api_error: {e}")

        score = parse_score(resp.text)
        log.info(
            "subjective.scored",
            backend=provider.name,
            model=provider.model,
            composite=round(score.composite, 3),
            cost_usd=getattr(resp, "cost_usd", 0.0),
        )
        return score

    async def score_corpus(
        self,
        slides: list[tuple[bytes, bytes]],
    ) -> list[SubjectiveScore]:
        """Score a list of (source_png, candidate_png) pairs.

        Caps at `self.max_slides` to keep cost predictable. Slides beyond the
        cap receive a zero-score with notes='skipped_max_slides'. Returns a
        list the same length as `slides`.
        """
        if not slides:
            return []

        out: list[SubjectiveScore] = []
        for i, (src, cand) in enumerate(slides):
            if i >= self.max_slides:
                out.append(SubjectiveScore.zero(notes="skipped_max_slides"))
                continue
            score = await self.score_slide(src, cand)
            out.append(score)
        return out
