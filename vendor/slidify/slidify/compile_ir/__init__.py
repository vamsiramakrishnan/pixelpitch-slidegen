"""IR to PPTX compiler package."""

from slidify.compile_ir.compiler import (
    NS_A,
    SLIDIFY_ESCAPE_NS,
    SLIDIFY_RECIPE_NS,
    EscapeMetering,
    _fetch_picture,
    compile_ir,
    compile_ir_file,
)

__all__ = [
    "NS_A",
    "SLIDIFY_ESCAPE_NS",
    "SLIDIFY_RECIPE_NS",
    "EscapeMetering",
    "_fetch_picture",
    "compile_ir",
    "compile_ir_file",
]

