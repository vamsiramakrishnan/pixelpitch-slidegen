"""Compatibility exports for harvester signal helpers.

New code should import from `cluster_signals`, `quality`, or `portfolio`
directly. This module keeps older imports stable.
"""

from slidify.harvester.cluster_signals import cluster_pipeline_signals
from slidify.harvester.portfolio import run_pipeline_signals
from slidify.harvester.quality import (
    _deck_summary_to_dict,
    _quality_issue_to_dict,
    deck_telemetry_from_result,
    merge_quality_issues,
    quality_issues_from_result,
)

__all__ = [
    "_deck_summary_to_dict",
    "_quality_issue_to_dict",
    "cluster_pipeline_signals",
    "deck_telemetry_from_result",
    "merge_quality_issues",
    "quality_issues_from_result",
    "run_pipeline_signals",
]
