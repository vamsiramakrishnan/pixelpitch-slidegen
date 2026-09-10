"""DOM walking, unit clustering, and coverage auditing."""

from slidify.dom.coverage import find_coverage_gaps
from slidify.dom.units import cluster, flatten
from slidify.dom.walker import SVG_NATIVE_PATH_BUDGET, WALKER_JS, walk

__all__ = [
    "SVG_NATIVE_PATH_BUDGET",
    "WALKER_JS",
    "cluster",
    "find_coverage_gaps",
    "flatten",
    "walk",
]

