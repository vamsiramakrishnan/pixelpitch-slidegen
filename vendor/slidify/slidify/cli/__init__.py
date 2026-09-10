"""Public CLI package."""

from slidify.cli.main import (
    _build_promotion_stubs,
    cli,
    harvest,
    main,
    prime_atom_cache_cmd,
    promote_unmatched_to_yaml,
)

__all__ = [
    "_build_promotion_stubs",
    "cli",
    "harvest",
    "main",
    "prime_atom_cache_cmd",
    "promote_unmatched_to_yaml",
]

