"""Configuration model for the slidify conversion API."""

from __future__ import annotations

from dataclasses import dataclass

from slidify.cache import StructuralCache
from slidify.geom import SLIDE_H_PX, SLIDE_W_PX
from slidify.progress import ProgressCallback


@dataclass
class ConversionConfig:
    """User-facing configuration for ``convert``."""

    viewport: tuple[int, int] = (SLIDE_W_PX, SLIDE_H_PX)
    run_oracle: bool = True
    run_tier3: bool = True
    llm_backend: str | None = None
    llm_model: str | None = None
    google_project: str | None = None
    google_location: str | None = None
    cache: StructuralCache | None = None
    max_oracle_iterations: int = 2
    render_concurrency: int = 4
    keep_plans_for_oracle: bool = True
    differential_render: bool = True
    embed_fonts: bool = True
    run_editability_check: bool = True
    progress_callback: ProgressCallback | None = None

    @classmethod
    def fast(cls) -> ConversionConfig:
        """Lowest-latency profile: no oracle, no LLM, no editability check."""
        return cls(
            run_oracle=False,
            run_tier3=False,
            run_editability_check=False,
            keep_plans_for_oracle=False,
        )

    @classmethod
    def from_env(cls) -> ConversionConfig:
        """Build a config from ``SLIDIFY_*`` environment variables."""
        import os

        def _flag(name: str) -> bool:
            return os.environ.get(name, "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        cfg = cls()
        if _flag("SLIDIFY_NO_ORACLE"):
            cfg.run_oracle = False
        if _flag("SLIDIFY_NO_TIER3"):
            cfg.run_tier3 = False
        if _flag("SLIDIFY_LOW_MEMORY"):
            cfg.keep_plans_for_oracle = False
        if _flag("SLIDIFY_NO_FONTS"):
            cfg.embed_fonts = False
        if backend := os.environ.get("SLIDIFY_LLM_BACKEND"):
            cfg.llm_backend = backend
        if model := os.environ.get("SLIDIFY_LLM_MODEL"):
            cfg.llm_model = model
        if rc := os.environ.get("SLIDIFY_RENDER_CONCURRENCY"):
            try:
                cfg.render_concurrency = max(1, int(rc))
            except ValueError:
                pass
        return cfg

