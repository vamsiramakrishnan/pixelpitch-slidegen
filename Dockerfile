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

FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./

COPY ./agy-worker/pyproject.toml ./agy-worker/uv.lock* ./agy-worker/

COPY ./app ./app

RUN uv sync --frozen

# AGY SDK requires Protobuf 7 while ADK/Agent Engine currently requires
# Protobuf <7. Keep the SDK in a separate locked environment so the A2A/A2UI
# shell and every existing Pixelpitch tool remain intact.
RUN uv sync --project /code/agy-worker --frozen --no-install-project

# Every module, not just runner.py. runner.py imports guards, disclosure and
# aids as siblings, and a copy list naming them one by one goes stale the next
# time one is added: the image builds, the container starts, and the first
# authoring turn dies on ImportError.
COPY ./agy-worker/*.py ./agy-worker/
COPY ./agy-worker/skills ./agy-worker/skills

ENV SLIDEGEN_AGY_PYTHON=/code/agy-worker/.venv/bin/python \
    SLIDEGEN_AGY_RUNNER=/code/agy-worker/runner.py

ARG COMMIT_SHA=""
ENV COMMIT_SHA=${COMMIT_SHA}

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
