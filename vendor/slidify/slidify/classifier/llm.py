"""LLM provider abstraction for tier-3 adjudication.

Supports four backends:
  * "gemini-aistudio" — Google AI Studio (GEMINI_API_KEY)
  * "gemini-vertex"   — Vertex AI (GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION)
  * "anthropic"       — Anthropic API (ANTHROPIC_API_KEY)
  * "claude-vertex"   — Claude on Vertex AI (Anthropic SDK in Vertex mode)

All providers share the same interface: classify_regions(...) returns the raw
text response. Image inputs are passed as PNG bytes; the provider handles
encoding.

Pricing constants below are best-effort approximations as of early 2026.
"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)


# Default models per provider. Callers may override. Flash is the default for
# Gemini backends because tier-3 only fires on the ambiguous tail (~10% of
# units) and Flash is ~16x cheaper than Pro at near-equivalent vision quality
# for this classification task.
DEFAULT_MODELS: dict[str, str] = {
    "gemini-aistudio": "gemini-2.5-flash",
    "gemini-vertex": "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
    "claude-vertex": "claude-haiku-4-5",
}

# Higher-quality alternates for `--llm-model` overrides or callers who want
# maximum decision quality at higher cost.
QUALITY_MODELS: dict[str, str] = {
    "gemini-aistudio": "gemini-2.5-pro",
    "gemini-vertex": "gemini-2.5-pro",
    "anthropic": "claude-opus-4-7",
    "claude-vertex": "claude-opus-4-7@20260101",
}

# (input $/M tokens, output $/M tokens) — rough estimates
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.075, 0.30),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LLMProvider(ABC):
    name: str = "abstract"

    def __init__(self, model: str, timeout_s: float = 60.0) -> None:
        self.model = model
        self.timeout_s = timeout_s

    @abstractmethod
    async def classify(
        self,
        system: str,
        user_text: str,
        images: list[bytes],
    ) -> LLMResponse:
        """Run the model on (system + user_text + images) and return the text."""

    def _estimate_cost(self, in_tok: int, out_tok: int) -> float:
        # Match by exact model first, then prefix
        price = PRICING.get(self.model)
        if price is None:
            for k, v in PRICING.items():
                if self.model.startswith(k):
                    price = v
                    break
        if price is None:
            return 0.0
        in_p, out_p = price
        return (in_tok / 1_000_000) * in_p + (out_tok / 1_000_000) * out_p


# --- Anthropic API (direct) ----------------------------------------------------


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODELS["anthropic"],
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__(model, timeout_s)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_s)

    async def classify(
        self, system: str, user_text: str, images: list[bytes]
    ) -> LLMResponse:
        content: list[dict] = [{"type": "text", "text": user_text}]
        for png in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    },
                }
            )
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        in_tok = getattr(resp.usage, "input_tokens", 0) if resp.usage else 0
        out_tok = getattr(resp.usage, "output_tokens", 0) if resp.usage else 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._estimate_cost(in_tok, out_tok),
        )


# --- Claude on Vertex AI -------------------------------------------------------


class ClaudeVertexProvider(LLMProvider):
    name = "claude-vertex"

    def __init__(
        self,
        model: str = DEFAULT_MODELS["claude-vertex"],
        timeout_s: float = 60.0,
        project_id: str | None = None,
        region: str | None = None,
    ) -> None:
        super().__init__(model, timeout_s)
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.region = region or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-east5")
        if not self.project_id:
            raise RuntimeError(
                "Claude-on-Vertex requires GOOGLE_CLOUD_PROJECT (or project_id arg)"
            )
        from anthropic import AsyncAnthropicVertex

        self._client = AsyncAnthropicVertex(
            project_id=self.project_id, region=self.region, timeout=timeout_s
        )

    async def classify(
        self, system: str, user_text: str, images: list[bytes]
    ) -> LLMResponse:
        content: list[dict] = [{"type": "text", "text": user_text}]
        for png in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(png).decode("ascii"),
                    },
                }
            )
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        in_tok = getattr(resp.usage, "input_tokens", 0) if resp.usage else 0
        out_tok = getattr(resp.usage, "output_tokens", 0) if resp.usage else 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._estimate_cost(in_tok, out_tok),
        )


# --- Gemini via AI Studio ------------------------------------------------------


class GeminiAIStudioProvider(LLMProvider):
    name = "gemini-aistudio"

    def __init__(
        self,
        model: str = DEFAULT_MODELS["gemini-aistudio"],
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__(model, timeout_s)
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
            "GOOGLE_API_KEY"
        )
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set")
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._genai = genai

    async def classify(
        self, system: str, user_text: str, images: list[bytes]
    ) -> LLMResponse:
        from google.genai import types as gtypes

        parts: list = [user_text]
        for png in images:
            parts.append(gtypes.Part.from_bytes(data=png, mime_type="image/png"))

        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            max_output_tokens=2048,
            response_mime_type="application/json",
        )
        resp = await self._client.aio.models.generate_content(
            model=self.model,
            contents=parts,
            config=config,
        )
        text = (resp.text or "").strip()
        in_tok = 0
        out_tok = 0
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            in_tok = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._estimate_cost(in_tok, out_tok),
        )


# --- Gemini via Vertex AI ------------------------------------------------------


class GeminiVertexProvider(LLMProvider):
    name = "gemini-vertex"

    def __init__(
        self,
        model: str = DEFAULT_MODELS["gemini-vertex"],
        timeout_s: float = 60.0,
        project_id: str | None = None,
        location: str | None = None,
    ) -> None:
        super().__init__(model, timeout_s)
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        if not self.project_id:
            raise RuntimeError(
                "Gemini-on-Vertex requires GOOGLE_CLOUD_PROJECT (or project_id arg)"
            )
        from google import genai

        self._client = genai.Client(
            vertexai=True, project=self.project_id, location=self.location
        )

    async def classify(
        self, system: str, user_text: str, images: list[bytes]
    ) -> LLMResponse:
        from google.genai import types as gtypes

        parts: list = [user_text]
        for png in images:
            parts.append(gtypes.Part.from_bytes(data=png, mime_type="image/png"))

        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            max_output_tokens=2048,
            response_mime_type="application/json",
        )
        resp = await self._client.aio.models.generate_content(
            model=self.model,
            contents=parts,
            config=config,
        )
        text = (resp.text or "").strip()
        in_tok = 0
        out_tok = 0
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            in_tok = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._estimate_cost(in_tok, out_tok),
        )


# --- Factory -------------------------------------------------------------------


def build_provider(
    backend: str,
    *,
    model: str | None = None,
    timeout_s: float = 60.0,
    project_id: str | None = None,
    location: str | None = None,
) -> LLMProvider:
    """Construct a provider by name.

    Backends:
        gemini-aistudio | gemini-vertex | anthropic | claude-vertex
    """
    backend = backend.lower()
    chosen_model = model or DEFAULT_MODELS.get(backend)
    if chosen_model is None:
        raise ValueError(f"unknown backend: {backend!r}")
    if backend == "anthropic":
        return AnthropicProvider(chosen_model, timeout_s)
    if backend == "claude-vertex":
        return ClaudeVertexProvider(
            chosen_model, timeout_s, project_id=project_id, region=location
        )
    if backend == "gemini-aistudio":
        return GeminiAIStudioProvider(chosen_model, timeout_s)
    if backend == "gemini-vertex":
        return GeminiVertexProvider(
            chosen_model, timeout_s, project_id=project_id, location=location
        )
    raise ValueError(f"unknown backend: {backend!r}")


def auto_select_backend() -> str | None:
    """Pick the first available backend based on environment.

    Order: gemini-aistudio → gemini-vertex → anthropic → claude-vertex.
    Returns None if none configured (caller falls back to safe defaults).
    """
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini-aistudio"
    if os.environ.get("GOOGLE_CLOUD_PROJECT") and os.environ.get("SLIDIFY_PREFER_VERTEX_GEMINI"):
        return "gemini-vertex"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return "claude-vertex"
    return None
