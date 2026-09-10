"""`python -m slidify` entrypoint.

Mirrors the `slidify` console script registered in `pyproject.toml` so the
CLI is reachable without a venv-installed entry point (handy in CI, sandboxed
agents, and one-off `uv run` invocations).
"""

from __future__ import annotations

from slidify.cli import main

if __name__ == "__main__":  # pragma: no cover
    main()
