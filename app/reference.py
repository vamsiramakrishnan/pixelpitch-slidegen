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

"""Derive a usable design system from a reference deck.

A reference deck is fundamentally a BRAND input — palette, type personality,
layout language — which is the same shape the authoring step already consumes.
So instead of a bolt-on "template" mode, an ingested deck is turned into a
synthesized guideline and used exactly like a curated brand.

Two honesty constraints shape this module:

* Extraction from rendered pages is INFERENCE, not fact. The derived system is
  always shown back to the user as a specimen before a deck is generated on it.
* The reference's own defects are recorded (``tells``) rather than silently
  absorbed, so template-fidelity mode can report what debt is being imported.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re

import httpx
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

REFERENCE_MODEL = os.getenv("SLIDEGEN_REFERENCE_MODEL", "gemini-3.7-flash")
EXTRACT_TIMEOUT_SECONDS = float(os.getenv("SLIDEGEN_EXTRACT_TIMEOUT", "600"))

_EXTRACT_RUBRIC = """
You are a design director reverse-engineering a presentation's design system so
another tool can generate NEW slides that look like they belong to the same
deck.

You are shown the rendered pages of a reference deck, in order.

Derive the system. Report what is ACTUALLY THERE, not what would be good.

Return ONLY this JSON object:

{
  "name": "<short name for this visual identity>",
  "summary": "<one sentence describing the look>",
  "palette": {"background": "#RRGGBB", "surface": "#RRGGBB",
              "ink": "#RRGGBB", "accent": "#RRGGBB",
              "accent_alt": "#RRGGBB"},
  "typography": {"display": "<face or closest web-safe equivalent>",
                 "body": "<face>",
                 "display_weight": "<e.g. 800>",
                 "case": "<e.g. sentence case / all caps headlines>"},
  "layout": {"grid": "<how content is arranged>",
             "density": "sparse|balanced|dense",
             "title_treatment": "<where and how titles sit>",
             "decoration": "<recurring decorative devices>"},
  "slide_archetypes": ["<recurring slide layouts you can see>"],
  "tells": ["<design tics this deck uses, e.g. an uppercase eyebrow above
             every heading, 01/02/03 section numbers, identical card grids>"],
  "confidence": "high|medium|low"
}

Colours MUST be hex sampled from the pages. If the deck is inconsistent, report
the dominant system and set confidence accordingly.
"""


def fetch_reference(
    *,
    renderer_url: str,
    headers: dict,
    source_url: str = "",
    file_b64: str = "",
    max_pages: int = 8,
) -> tuple[list[bytes], dict, str | None]:
    """Rasterise a reference deck. Returns ``(page_pngs, theme, error)``."""
    payload = {
        "source_url": source_url,
        "file_b64": file_b64,
        "max_pages": max_pages,
    }
    try:
        response = httpx.post(
            f"{renderer_url.rstrip('/')}/extract",
            json=payload,
            headers=headers,
            timeout=EXTRACT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return [], {}, f"Could not reach the extraction service: {exc}"

    if data.get("ok") is not True:
        return [], {}, data.get("error") or "Extraction failed."

    try:
        pages = [base64.b64decode(b) for b in data.get("images_b64", [])]
    except Exception:
        return [], {}, "Extracted pages could not be decoded."
    if not pages:
        return [], {}, "No pages could be rendered from that deck."
    return pages, data.get("theme") or {}, None


def _parse_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def derive_system(pages: list[bytes], theme: dict) -> tuple[dict | None, str | None]:
    """Infer the design system from rendered pages plus any OOXML theme data."""
    parts: list[types.Part] = []
    if theme:
        # Font names read out of the file itself beat anything inferred from
        # pixels, so hand them to the model as ground truth.
        hint = {
            k: theme.get(k)
            for k in ("fonts", "n_slides", "slide_width_emu", "slide_height_emu")
            if theme.get(k)
        }
        if hint:
            parts.append(
                types.Part.from_text(
                    text=(
                        "Ground truth extracted from the source file (trust "
                        f"these over your reading of the pixels): {json.dumps(hint)}"
                    )
                )
            )
    for i, png in enumerate(pages):
        parts.append(types.Part.from_text(text=f"Page {i + 1}:"))
        parts.append(types.Part.from_bytes(data=png, mime_type="image/png"))

    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=REFERENCE_MODEL,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=_EXTRACT_RUBRIC,
                temperature=0.2,
                max_output_tokens=8_000,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        logger.exception("reference extraction call failed")
        return None, f"Could not analyse the reference deck: {exc}"

    system = _parse_object(response.text or "")
    if not system:
        return None, "The reference deck could not be interpreted as a design system."
    return system, None


def to_guideline_markdown(system: dict) -> str:
    """Render a derived system in the same shape as a curated brand guideline."""
    pal = system.get("palette") or {}
    typo = system.get("typography") or {}
    lay = system.get("layout") or {}

    def _rows(d: dict) -> str:
        return "\n".join(f"- **{k}**: `{v}`" for k, v in d.items() if v)

    archetypes = "\n".join(
        f"- {a}" for a in (system.get("slide_archetypes") or []) if a
    )
    tells = "\n".join(f"- {t}" for t in (system.get("tells") or []) if t)

    return f"""# Design System Derived From Reference Deck

> {system.get("summary", "")}
> Extraction confidence: {system.get("confidence", "unknown")}

This system was inferred from a reference deck supplied by the user. Match it.

## 1. Colour Palette & Roles
{_rows(pal) or "- (not detected)"}

## 2. Typography Rules
### Font Family
{_rows(typo) or "- (not detected)"}

## 3. Layout Principles
{_rows(lay) or "- (not detected)"}

## 4. Slide Archetypes
{archetypes or "- (none identified)"}

## 5. Recurring Devices In The Source Deck
These are the reference deck's own habits. In template-fidelity mode they are
reproduced deliberately because the user asked to match this deck.
{tells or "- (none identified)"}
"""


def ingest(
    *,
    renderer_url: str,
    headers: dict,
    source_url: str = "",
    file_b64: str = "",
) -> tuple[dict | None, str | None]:
    """Full pipeline: fetch, rasterise, infer. Returns ``(reference, error)``."""
    pages, theme, error = fetch_reference(
        renderer_url=renderer_url,
        headers=headers,
        source_url=source_url,
        file_b64=file_b64,
    )
    if error:
        return None, error

    system, error = derive_system(pages, theme)
    if error:
        return None, error

    system["page_count"] = len(pages)
    system["guideline"] = to_guideline_markdown(system)
    if theme.get("fonts"):
        system["source_fonts"] = theme["fonts"]
    return system, None
