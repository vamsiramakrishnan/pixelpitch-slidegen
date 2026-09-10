"""slidify — render-and-classify HTML to PPTX conversion.

Public API:

    from slidify import convert, convert_sync, ConversionConfig, ConversionResult

    cfg = ConversionConfig.fast()              # latency-first profile
    result = convert_sync("deck.html", "deck.pptx", cfg)

`convert` is the async entrypoint, `convert_sync` a wrapper for plain scripts.
For containers and CI, ``ConversionConfig.from_env()`` reads ``SLIDIFY_*`` env
vars (see its docstring).
"""

from slidify._logging import ensure_configured as _ensure_logging_configured
from slidify.api import (
    ConversionConfig,
    ConversionResult,
    convert,
    convert_sync,
)

# Route logs to stderr before any module gets a chance to log to stdout.
_ensure_logging_configured()
from slidify.exceptions import (  # noqa: E402
    ClassificationError,
    EmitError,
    OracleError,
    RenderError,
    SlidifyError,
)

__all__ = [
    "ClassificationError",
    "ConversionConfig",
    "ConversionResult",
    "EmitError",
    "OracleError",
    "RenderError",
    "SlidifyError",
    "convert",
    "convert_sync",
]
__version__ = "0.1.0"
