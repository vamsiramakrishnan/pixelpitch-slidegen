#!/usr/bin/env bash
# Convert authored slides to a real PPTX via the slidegen renderer, download
# it, and extract preview PNGs. Mechanical delivery step — the *decision* to
# ship stays with the agent's quality gates.
#
# Usage: render_deck.sh <slides_json> <outdir>
#   slides_json: [{"title": ..., "role": ..., "html": "<!doctype html>..."}, ...]
# Env: SLIDEGEN_RENDERER_URL (default http://127.0.0.1:18099)
#      SLIDEGEN_DECK_TITLE (default "Deck")
#      SLIDEGEN_BRAND_ID   (default "derived")
set -euo pipefail

slides_json="${1:?usage: render_deck.sh <slides_json> <outdir>}"
outdir="${2:?usage: render_deck.sh <slides_json> <outdir>}"
renderer="${SLIDEGEN_RENDERER_URL:-http://127.0.0.1:18099}"
title="${SLIDEGEN_DECK_TITLE:-Deck}"
brand="${SLIDEGEN_BRAND_ID:-derived}"

mkdir -p "$outdir/preview"

python3 - "$slides_json" "$outdir" "$renderer" "$title" "$brand" <<'PY'
import base64, json, sys, urllib.request
from pathlib import Path

slides_json, outdir, renderer, title, brand = sys.argv[1:6]
slides = json.loads(Path(slides_json).read_text(encoding="utf-8"))
request = urllib.request.Request(
    f"{renderer}/render",
    data=json.dumps(
        {
            "deck_title": title,
            "brand_id": brand,
            "slides": slides,
            "preview_pages": min(len(slides), 6),
        }
    ).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=900) as response:
    payload = json.loads(response.read().decode("utf-8"))
if payload.get("ok") is not True:
    sys.exit(f"render failed: {payload.get('error')}")
out = Path(outdir)
(out / "render.json").write_text(
    json.dumps(
        {
            "gs_uri": payload.get("gs_uri"),
            "slide_count": payload.get("slide_count"),
            "native_area_ratio": payload.get("native_area_ratio"),
            "quality": payload.get("quality"),
        },
        indent=2,
    ),
    encoding="utf-8",
)
for i, b64 in enumerate(payload.get("images_b64") or []):
    (out / "preview" / f"slide_{i + 1:02d}.png").write_bytes(base64.b64decode(b64))
print(payload.get("gs_uri") or "")
PY

gs_uri="$(python3 -c "import json,sys;print(json.load(open('$outdir/render.json'))['gs_uri'])")"
if [[ -n "$gs_uri" ]]; then
  gsutil cp "$gs_uri" "$outdir/deck.pptx"
fi
echo "rendered deck.pptx + previews in $outdir"
