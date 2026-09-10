#!/usr/bin/env bash
# Stage a customer PPTX template for brand derivation.
#
# Mechanical I/O only — the *understanding* is the harness's job, driven by
# this skill. Fetches the template from GCS, converts it to PDF, and renders
# every slide to refs/ so the harness can inspect the visual language.
#
# Usage: stage_template.sh <gs_uri> <workdir>
set -euo pipefail

gs_uri="${1:?usage: stage_template.sh <gs_uri> <workdir>}"
workdir="${2:?usage: stage_template.sh <gs_uri> <workdir>}"

mkdir -p "$workdir/refs"

gsutil cp "$gs_uri" "$workdir/template.pptx"

soffice --headless --convert-to pdf --outdir "$workdir" "$workdir/template.pptx"

pdf="$(compgen -G "$workdir/*.pdf" | head -n1)"
if [[ -z "${pdf:-}" ]]; then
  echo "stage_template: no PDF produced" >&2
  exit 1
fi

pdftoppm -png -r 90 "$pdf" "$workdir/refs/slide"

count="$(find "$workdir/refs" -name 'slide*.png' | wc -l)"
echo "staged $count rendered pages in $workdir/refs"
