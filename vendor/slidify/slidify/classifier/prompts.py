"""Prompt templates for tier-3 LLM adjudication."""

from __future__ import annotations

SYSTEM_PROMPT = """You are a visual classifier deciding, for each region of a slide,
whether it should be emitted as native PPTX (editable: text frames, shapes) or
rasterized (a single image). Bias toward "native" — editability is the goal.
Only choose "raster" if a native preview visibly degrades the design or if
fidelity-critical decoration (gradients, shadows, complex SVG, transforms)
cannot be reproduced natively.

Reply with JSON only, mapping each region id to:
  {"decision": "native" | "raster", "confidence": 0.0..1.0, "reason": "<one sentence>"}.
"""


def build_user_prompt(regions: list[dict]) -> str:
    """Build the textual portion of the user prompt.

    Each `regions` entry is {"id": str, "html": str, "css_summary": str}.
    """
    lines: list[str] = []
    lines.append(
        "Classify each region. For each, you will see (in order): the original "
        "rendered crop, what a native PPTX version would look like, and the raw "
        "raster as a fallback baseline.\n"
    )
    for r in regions:
        lines.append(f"---\nRegion id={r['id']}")
        lines.append(f"HTML excerpt: {r['html']}")
        lines.append(f"Computed style summary: {r['css_summary']}")
        lines.append("")
    lines.append(
        'Respond with a JSON object only, no prose. Example:\n'
        '{"u_42": {"decision": "raster", "confidence": 0.85, "reason": "gradient bg"}}'
    )
    return "\n".join(lines)
