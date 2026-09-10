FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY mcp-app/package.json mcp-app/package-lock.json ./
RUN npm ci
COPY mcp-app/index.html mcp-app/host.html mcp-app/tsconfig.json mcp-app/vite.config.ts ./
COPY mcp-app/src ./src
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.8.13
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep
WORKDIR /code
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --frozen --extra mcp-app --no-dev
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/chromium
RUN uv run --no-sync python -m playwright install --with-deps chromium

# Preserve the native authoring runtime's separate Protobuf environment.
COPY agy-worker/pyproject.toml agy-worker/uv.lock ./agy-worker/
RUN uv sync --project /code/agy-worker --frozen --no-install-project --no-dev
COPY agy-worker/*.py ./agy-worker/
COPY agy-worker/skills ./agy-worker/skills
COPY --from=ui /ui/dist/index.html ./mcp-app/dist/index.html

ENV SLIDEGEN_AGY_PYTHON=/code/agy-worker/.venv/bin/python \
    SLIDEGEN_AGY_RUNNER=/code/agy-worker/runner.py \
    PIXELPITCH_DECK_PYTHON=/code/.venv/bin/python \
    PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home pixelpitch && chown -R pixelpitch:pixelpitch /code
USER pixelpitch
EXPOSE 8080
CMD ["uv", "run", "--no-sync", "uvicorn", "app.mcp_cloud:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
