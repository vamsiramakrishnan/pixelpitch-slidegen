"""Corpus harvesting and renderer-improvement signal extraction.

The public API intentionally mirrors the former `slidify.harvester` module so
existing CLI/tests can keep importing `aggregate_corpus`, `cluster_signatures`,
`report_to_dict`, and the report dataclasses from here.
"""

from slidify.harvester.candidates import (
    cluster_signatures,
    propose_atom_candidate,
)
from slidify.harvester.collection import (
    _harvest_one,
    _harvest_one_async,
    _harvest_result,
    _harvest_result_async,
)
from slidify.harvester.core import aggregate_corpus, report_to_dict, write_report
from slidify.harvester.mechanisms import (
    Mechanism,
    mechanisms_to_dict,
    top_mechanisms,
)
from slidify.harvester.signals import (
    cluster_pipeline_signals,
    deck_telemetry_from_result,
    merge_quality_issues,
    quality_issues_from_result,
    run_pipeline_signals,
)
from slidify.harvester.types import (
    AtomCandidate,
    Cluster,
    ClusterExemplar,
    DeckTelemetry,
    HarvestReport,
    QualityIssue,
)

__all__ = [
    "AtomCandidate",
    "Cluster",
    "ClusterExemplar",
    "DeckTelemetry",
    "HarvestReport",
    "Mechanism",
    "QualityIssue",
    "_harvest_one",
    "_harvest_one_async",
    "_harvest_result",
    "_harvest_result_async",
    "aggregate_corpus",
    "cluster_pipeline_signals",
    "cluster_signatures",
    "deck_telemetry_from_result",
    "mechanisms_to_dict",
    "merge_quality_issues",
    "propose_atom_candidate",
    "quality_issues_from_result",
    "report_to_dict",
    "run_pipeline_signals",
    "top_mechanisms",
    "write_report",
]
