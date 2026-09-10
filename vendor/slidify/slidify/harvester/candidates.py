from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from slidify.harvester.types import AtomCandidate, Cluster, ClusterExemplar
from slidify.models import UnmatchedSignature


def cluster_signatures(
    observations: Iterable[tuple[str, UnmatchedSignature]],
    *,
    min_occurrences: int = 1,
) -> list[Cluster]:
    """Group `UnmatchedSignature` observations into clusters.

    `observations` yields `(deck_ref, sig)` tuples — `deck_ref` is the
    string used as the exemplar's slide reference (typically the corpus-
    relative file path, optionally `#node-<i>`).

    For v1 we cluster by exact `sig_hash` equality. The `signature` string
    already normalizes class names + quantizes bboxes (see
    `slidify/patterns/signatures.py::signature`), so two visually identical
    units land on the same hash even if their text or exact px sizes differ.

    `min_occurrences` filters out singleton noise; defaults to 1 (keep all).
    """
    by_hash: dict[str, dict[str, Any]] = {}
    for deck_ref, sig in observations:
        h = sig.sig_hash
        bucket = by_hash.setdefault(
            h,
            {
                "sig_hash": h,
                "signature": sig.sig,
                "instances": 0,
                "exemplars": [],
                "sample_classes": [],
                "sample_text": [],
                "bbox_w": [],
                "bbox_h": [],
                "source_files": set(),
                "source_groups": set(),
            },
        )
        bucket["instances"] += int(sig.n_occurrences)
        bucket["bbox_w"].append(int(sig.bbox_w))
        bucket["bbox_h"].append(int(sig.bbox_h))
        source_file = deck_ref.split("#", 1)[0]
        source_group = source_file.split("/", 1)[0] if "/" in source_file else "."
        bucket["source_files"].add(source_file)
        bucket["source_groups"].add(source_group)
        if sig.sample_classes and sig.sample_classes not in bucket["sample_classes"]:
            bucket["sample_classes"].append(sig.sample_classes)
        if sig.sample_text and sig.sample_text not in bucket["sample_text"]:
            bucket["sample_text"].append(sig.sample_text)
        bucket["exemplars"].append(
            ClusterExemplar(
                slide_ref=deck_ref,
                bbox_w=int(sig.bbox_w),
                bbox_h=int(sig.bbox_h),
                sample_text=sig.sample_text,
                sample_classes=sig.sample_classes,
            )
        )

    clusters: list[Cluster] = []
    for i, (_, b) in enumerate(
        sorted(by_hash.items(), key=lambda kv: -kv[1]["instances"]), start=1
    ):
        if b["instances"] < min_occurrences:
            continue
        ws, hs = b["bbox_w"], b["bbox_h"]
        bbox_typical = {
            "w_avg": round(sum(ws) / len(ws), 1) if ws else 0.0,
            "h_avg": round(sum(hs) / len(hs), 1) if hs else 0.0,
            "w_min": min(ws) if ws else 0,
            "w_max": max(ws) if ws else 0,
            "h_min": min(hs) if hs else 0,
            "h_max": max(hs) if hs else 0,
        }
        clusters.append(
            Cluster(
                id=f"auto-cluster-{i:03d}",
                sig_hash=b["sig_hash"],
                signature=b["signature"],
                instances=b["instances"],
                # Cap exemplars to keep clusters.json bounded on huge corpora.
                exemplars=b["exemplars"][:10],
                sample_classes=b["sample_classes"][:5],
                sample_text=b["sample_text"][:5],
                bbox_typical=bbox_typical,
                source_files=sorted(b["source_files"])[:25],
                source_groups=sorted(b["source_groups"]),
            )
        )
    return clusters


# -----------------------------------------------------------------------------
# Candidate atom proposal (heuristic — designer reviews)
# -----------------------------------------------------------------------------


# Class-name keywords → (axis, atom-id-stem). Order matters: more specific
# keywords come first so e.g. "stat-card" lands as data.* rather than surf.*.
_AXIS_KEYWORDS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\bstat(?:-|s\b|\b)"),     "data",   "stat"),
    (re.compile(r"\bnumber\b"),             "data",   "number"),
    (re.compile(r"\bmetric\b"),             "data",   "metric"),
    (re.compile(r"\bchart\b|\bgraph\b"),    "data",   "chart"),
    (re.compile(r"\btable\b"),              "data",   "table"),
    (re.compile(r"\bgauge\b"),              "data",   "gauge"),
    (re.compile(r"\bicon\b"),               "dec",    "icon"),
    (re.compile(r"\bavatar\b"),             "dec",    "avatar"),
    (re.compile(r"\blogo\b"),               "dec",    "logo"),
    (re.compile(r"\bbadge\b"),              "dec",    "badge"),
    (re.compile(r"\bglyph\b"),              "dec",    "glyph"),
    (re.compile(r"\bcard\b"),               "surf",   "card"),
    (re.compile(r"\btile\b"),               "surf",   "tile"),
    (re.compile(r"\bpanel\b"),              "surf",   "panel"),
    (re.compile(r"\bbento\b"),              "surf",   "bento"),
    (re.compile(r"\bframe\b"),              "surf",   "frame"),
    (re.compile(r"\bsurface\b"),            "surf",   "surface"),
    (re.compile(r"\bhero\b"),               "comp",   "hero"),
    (re.compile(r"\bcta\b"),                "comp",   "cta"),
    (re.compile(r"\bquote\b"),              "comp",   "quote"),
    (re.compile(r"\btestimonial\b"),        "comp",   "testimonial"),
    (re.compile(r"\bfeature\b"),            "comp",   "feature"),
    (re.compile(r"\bpricing\b"),            "comp",   "pricing"),
    (re.compile(r"\bnav\b|\bnavbar\b"),     "chrome", "nav"),
    (re.compile(r"\bheader\b"),             "chrome", "header"),
    (re.compile(r"\bfooter\b"),             "chrome", "footer"),
    (re.compile(r"\bsidebar\b"),            "chrome", "sidebar"),
    (re.compile(r"\bpill\b"),               "text",   "pill"),
    (re.compile(r"\bkicker\b"),             "text",   "kicker"),
    (re.compile(r"\btitle\b"),              "text",   "title"),
    (re.compile(r"\bsubtitle\b"),           "text",   "subtitle"),
    (re.compile(r"\bcaption\b"),            "text",   "caption"),
    (re.compile(r"\bgrid\b"),               "layout", "grid"),
    (re.compile(r"\bstack\b"),              "layout", "stack"),
    (re.compile(r"\brow\b"),                "layout", "row"),
    (re.compile(r"\bcol(?:umn)?\b"),        "layout", "column"),
]

# Signature substrings → property bits. Read off the structural sig string
# directly so we don't need to re-parse the DOM.
_SIG_PROP_PROBES: list[tuple[str, str, Any]] = [
    ("brd",            "border",       True),
    ("shdw=t",         "shadow",       "translatable"),
    ("shdw=x",         "shadow",       "raster"),
    ("bgi=grad",       "gradient",     True),
    ("bgi=url",        "image",        True),
    ("xform",          "transform",    True),
    ("filter",         "filter",       True),
    ("svg/",           "svg",          True),
    ("canvas",         "canvas",       True),
    ("img",            "image",        True),
    ("video",          "video",        True),
    ("grid-line",      "grid",         True),
    ("::before",       "pseudo_before", True),
    ("::after",        "pseudo_after",  True),
]

_SLIDE_AREA = 1280 * 720


def _dominant_classes(cluster: Cluster) -> list[str]:
    """Return the cluster's class-name tokens ranked by frequency.

    `sample_classes` is a list of raw `class` attribute strings from each
    exemplar (e.g. "tile shadow-xl rounded-2xl"). We split + count tokens
    across every exemplar so common skeleton classes ("tile", "card") rise
    above one-off styling utilities.
    """
    counts: dict[str, int] = {}
    for ex in cluster.exemplars:
        for tok in (ex.sample_classes or "").split():
            tok = tok.strip()
            if not tok:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def propose_atom_candidate(cluster: Cluster) -> AtomCandidate:
    """Heuristically propose `(axis, atom_id, props)` for a cluster.

    Ranking signals (in priority order):

    1. Class-name keyword match (highest signal — authors named it).
    2. Anchor `kind` bits in the signature (svg / canvas / img → dec.*).
    3. Aspect ratio + bbox bucket (square small → dec.icon-*; square big →
       surf.tile-*; wide short → text.banner-*; etc.).
    4. Fallback: surf.unmatched-<short_hash> with axis "surf".

    The output's `confidence` is a rough self-rating (0..1) so downstream
    review tooling can sort proposals by how trustworthy the heuristic is.
    """
    classes = _dominant_classes(cluster)
    sig = cluster.signature or ""

    # 1) class-name keyword match
    for rx, axis, stem in _AXIS_KEYWORDS:
        for cls in classes:
            if rx.search(cls.lower()):
                props = _props_from_signature(sig)
                modifiers = _modifiers_from_props(props)
                atom_id = f"{axis}.{stem}" + (
                    f"-{'-'.join(modifiers)}" if modifiers else ""
                )
                return AtomCandidate(
                    candidate_atom_id=atom_id,
                    candidate_axis=axis,
                    candidate_props=props,
                    confidence=0.75,
                    reason=f"class '{cls}' matched /{rx.pattern}/",
                )

    # 2) anchor-kind sniff from signature
    if "svg/" in sig or "canvas" in sig:
        bbox_w = cluster.bbox_typical.get("w_avg", 0)
        bbox_h = cluster.bbox_typical.get("h_avg", 0)
        if bbox_w and bbox_h and max(bbox_w, bbox_h) <= 96:
            return AtomCandidate(
                candidate_atom_id="dec.icon-vector",
                candidate_axis="dec",
                candidate_props={"vector": True},
                confidence=0.6,
                reason="small svg/canvas bbox",
            )
        return AtomCandidate(
            candidate_atom_id="dec.illustration-vector",
            candidate_axis="dec",
            candidate_props={"vector": True},
            confidence=0.45,
            reason="large svg/canvas bbox",
        )

    if "img" in sig and "svg/" not in sig:
        return AtomCandidate(
            candidate_atom_id="media.image",
            candidate_axis="media",
            candidate_props={"raster": True},
            confidence=0.55,
            reason="anchor is <img>",
        )

    # 3) bbox-shape fallback
    bbox_w = cluster.bbox_typical.get("w_avg", 0)
    bbox_h = cluster.bbox_typical.get("h_avg", 0)
    aspect = (bbox_w / bbox_h) if bbox_h else 0
    props = _props_from_signature(sig)
    modifiers = _modifiers_from_props(props)

    if bbox_w and bbox_h:
        if 0.85 <= aspect <= 1.15 and max(bbox_w, bbox_h) <= 96:
            return AtomCandidate(
                candidate_atom_id="dec.glyph"
                + (f"-{'-'.join(modifiers)}" if modifiers else ""),
                candidate_axis="dec",
                candidate_props=props,
                confidence=0.4,
                reason="small square bbox",
            )
        if 0.85 <= aspect <= 1.25 and bbox_w >= 200:
            stem = "tile" + (f"-{'-'.join(modifiers)}" if modifiers else "")
            return AtomCandidate(
                candidate_atom_id=f"surf.{stem}",
                candidate_axis="surf",
                candidate_props=props,
                confidence=0.4,
                reason="square card-sized bbox",
            )
        if aspect >= 3.0:
            return AtomCandidate(
                candidate_atom_id="text.banner"
                + (f"-{'-'.join(modifiers)}" if modifiers else ""),
                candidate_axis="text",
                candidate_props=props,
                confidence=0.35,
                reason="wide-short bbox",
            )

    # 4) terminal fallback
    return AtomCandidate(
        candidate_atom_id=f"surf.unmatched-{cluster.sig_hash[:8]}",
        candidate_axis="surf",
        candidate_props=props,
        confidence=0.15,
        reason="no class/shape signal — falling back to sig_hash stem",
    )


def _props_from_signature(sig: str) -> dict[str, Any]:
    """Read structural props out of the signature string."""
    out: dict[str, Any] = {}
    for needle, key, value in _SIG_PROP_PROBES:
        if needle in sig and key not in out:
            out[key] = value
    return out


def _modifiers_from_props(props: dict[str, Any]) -> list[str]:
    """Stable, alphabetic list of modifier suffixes for an atom id stem."""
    bits: list[str] = []
    if props.get("border"):
        bits.append("bordered")
    if props.get("gradient"):
        bits.append("gradient")
    if props.get("grid"):
        bits.append("grid")
    if props.get("shadow"):
        bits.append("shadowed")
    if props.get("svg"):
        bits.append("vector")
    if props.get("transform"):
        bits.append("rotated")
    return sorted(bits)
