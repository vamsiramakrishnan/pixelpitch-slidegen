# The deployed renderer pins Slidify, source fonts, Chromium and LibreOffice.
ARG RENDERER_IMAGE
FROM ${RENDERER_IMAGE}

RUN pip install --no-cache-dir uv==0.8.13
WORKDIR /code
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
COPY agy-worker/pyproject.toml agy-worker/uv.lock ./agy-worker/
RUN uv sync --frozen --extra template-worker \
    && uv sync --project /code/agy-worker --frozen --no-install-project
COPY agy-worker/*.py ./agy-worker/
COPY agy-worker/skills ./agy-worker/skills

ENV SLIDEGEN_AGY_PYTHON=/code/agy-worker/.venv/bin/python \
    SLIDEGEN_AGY_RUNNER=/code/agy-worker/runner.py
CMD ["/code/.venv/bin/uvicorn", "app.template_worker:app", "--host", "0.0.0.0", "--port", "8080"]
