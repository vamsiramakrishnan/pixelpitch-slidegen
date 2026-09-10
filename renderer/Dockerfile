# Build from the REPOSITORY ROOT so both slidify and the renderer are in
# context:
#   docker build -f renderer/Dockerfile \
#     -t pixelpitch-slidegen-renderer .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        fonts-dejavu-core \
        fontconfig \
        ca-certificates \
        # slidify's fidelity oracle renders the produced .pptx and compares it
        # to the source (SSIM + OCR). Without LibreOffice every slide reports
        # `oracle_render_failed` and native_area_ratio is reported as 0.0 even
        # when the deck converted entirely to native shapes.
        libreoffice-impress \
        # pdftoppm, used to rasterise the oracle's PDF for comparison.
        poppler-utils \
        # Inter is the default face in the slide authoring grammar; without it
        # the oracle compares against a substituted font and reports drift.
        fonts-inter \
        fonts-roboto \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY licenses/ ./licenses/
COPY vendor/slidify /srv/slidify
COPY renderer /srv/renderer
COPY agy-worker/skills/pixelpitch-template-roundtrip/scripts/extract_slide_spec.py /opt/pixelpitch-template-tools/extract_slide_spec.py

RUN pip install --no-cache-dir /srv/slidify /srv/renderer \
    && playwright install --with-deps chromium \
    && mkdir -p /opt/ms-playwright

ENV SLIDIFY_PROFILE=full

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn renderer.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
