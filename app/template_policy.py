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

"""Immutable template admission and guarded HTML seam patching.

Prepared template artifacts are external input. This module validates them at
the boundary and exposes frozen domain values to the authoring pipeline. Taste
preferences never appear here: a baseline either belongs to the pinned source
revision and admits a patch, or it does not.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from typing import Literal


class TemplatePolicyError(ValueError):
    """A prepared bundle or seam patch failed admission."""


class DeckProvenance(StrEnum):
    HTML_RECONSTRUCTED_BY_SLIDIFY = "HTML_RECONSTRUCTED_BY_SLIDIFY"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_path(value: object) -> str:
    path = str(value or "").replace("\\", "/").strip("/")
    if not path or path.startswith(".") or "/../" in f"/{path}/":
        raise TemplatePolicyError(f"unsafe prepared artifact path: {value!r}")
    return path


@dataclass(frozen=True)
class SourceIdentity:
    gs_uri: str
    generation: str
    etag: str
    sha256: str

    @classmethod
    def from_dict(cls, value: object) -> SourceIdentity:
        if not isinstance(value, dict):
            raise TemplatePolicyError("manifest source must be an object")
        source = cls(
            gs_uri=str(value.get("gs_uri") or ""),
            generation=str(value.get("generation") or ""),
            etag=str(value.get("etag") or ""),
            sha256=str(value.get("sha256") or ""),
        )
        if not source.gs_uri.startswith("gs://"):
            raise TemplatePolicyError("manifest source gs_uri is invalid")
        if not source.generation or not source.etag:
            raise TemplatePolicyError("manifest source revision is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", source.sha256):
            raise TemplatePolicyError("manifest source sha256 is invalid")
        return source

    def to_dict(self) -> dict:
        return {
            "gs_uri": self.gs_uri,
            "generation": self.generation,
            "etag": self.etag,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ArtifactDigest:
    path: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_dict(cls, value: object) -> ArtifactDigest:
        if not isinstance(value, dict):
            raise TemplatePolicyError("manifest artifact must be an object")
        path = _safe_path(value.get("path"))
        try:
            size = int(value.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise TemplatePolicyError(f"artifact {path} has invalid size") from exc
        digest = str(value.get("sha256") or "")
        if size < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise TemplatePolicyError(f"artifact {path} has invalid digest metadata")
        return cls(path=path, size_bytes=size, sha256=digest)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class SeamSpec:
    id: str
    kind: Literal["content", "visual_aid"]
    allowed_tags: frozenset[str]
    allowed_style_properties: frozenset[str]

    @classmethod
    def from_dict(cls, value: object) -> SeamSpec:
        if not isinstance(value, dict):
            raise TemplatePolicyError("slide seam must be an object")
        seam_id = str(value.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", seam_id):
            raise TemplatePolicyError(f"invalid seam id: {seam_id!r}")
        kind = str(value.get("kind") or "")
        if kind not in {"content", "visual_aid"}:
            raise TemplatePolicyError(f"seam {seam_id} has invalid kind")
        tags = frozenset(str(tag).lower() for tag in (value.get("allowed_tags") or []))
        styles = frozenset(
            str(prop).lower()
            for prop in (value.get("allowed_style_properties") or [])
        )
        return cls(
            id=seam_id,
            kind=kind,  # type: ignore[arg-type]
            allowed_tags=tags,
            allowed_style_properties=styles,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "allowed_tags": sorted(self.allowed_tags),
            "allowed_style_properties": sorted(self.allowed_style_properties),
        }


@dataclass(frozen=True)
class AdmittedSlideType:
    id: str
    archetype: str
    baseline_path: str
    preview_path: str | None
    seams: tuple[SeamSpec, ...]
    source_slide: int | None = None

    @classmethod
    def from_dict(cls, value: object) -> AdmittedSlideType:
        if not isinstance(value, dict):
            raise TemplatePolicyError("slide type must be an object")
        slide_type = str(value.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", slide_type):
            raise TemplatePolicyError(f"invalid slide type id: {slide_type!r}")
        seams = tuple(SeamSpec.from_dict(item) for item in (value.get("seams") or []))
        if not seams:
            raise TemplatePolicyError(f"slide type {slide_type} declares no seams")
        if len({seam.id for seam in seams}) != len(seams):
            raise TemplatePolicyError(f"slide type {slide_type} duplicates a seam id")
        preview = value.get("preview_path")
        return cls(
            id=slide_type,
            archetype=str(value.get("archetype") or "body")[:80],
            baseline_path=_safe_path(value.get("baseline_path")),
            preview_path=_safe_path(preview) if preview else None,
            seams=seams,
            source_slide=value.get("source_slide"),
        )

    def to_dict(self) -> dict:
        value = {
            "id": self.id,
            "archetype": self.archetype,
            "baseline_path": self.baseline_path,
            "seams": [seam.to_dict() for seam in self.seams],
        }
        if self.preview_path:
            value["preview_path"] = self.preview_path
        if self.source_slide is not None:
            value["source_slide"] = self.source_slide
        return value


@dataclass(frozen=True)
class PreparedBundleManifest:
    schema_version: Literal[1]
    bundle_id: str
    source: SourceIdentity
    artifacts: tuple[ArtifactDigest, ...]
    slide_types: tuple[AdmittedSlideType, ...]

    @classmethod
    def from_dict(cls, value: object) -> PreparedBundleManifest:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise TemplatePolicyError("unsupported prepared bundle manifest")
        artifacts = tuple(
            ArtifactDigest.from_dict(item) for item in (value.get("artifacts") or [])
        )
        if not artifacts or len({item.path for item in artifacts}) != len(artifacts):
            raise TemplatePolicyError("manifest artifacts are empty or duplicated")
        slide_types = tuple(
            AdmittedSlideType.from_dict(item)
            for item in (value.get("slide_types") or [])
        )
        if not slide_types:
            raise TemplatePolicyError("prepared bundle has no admitted baseline specimens")
        if len({item.id for item in slide_types}) != len(slide_types):
            raise TemplatePolicyError("prepared bundle duplicates a slide type id")
        bundle_id = str(value.get("bundle_id") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", bundle_id):
            raise TemplatePolicyError("manifest bundle_id is invalid")
        return cls(
            schema_version=1,
            bundle_id=bundle_id,
            source=SourceIdentity.from_dict(value.get("source")),
            artifacts=artifacts,
            slide_types=slide_types,
        )

    def _identity_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "slide_types": [item.to_dict() for item in self.slide_types],
        }

    def expected_bundle_id(self) -> str:
        encoded = json.dumps(
            self._identity_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
        return sha256_bytes(encoded)

    def to_dict(self) -> dict:
        return {"bundle_id": self.bundle_id, **self._identity_payload()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


_MARKER = re.compile(
    r"<!--\s*pp:seam:([a-zA-Z0-9_.-]+):(start|end)\s*-->", re.I
)


def _seam_ranges(html: str, seams: tuple[SeamSpec, ...]) -> dict[str, tuple[int, int]]:
    declared = {seam.id for seam in seams}
    ranges: dict[str, tuple[int, int]] = {}
    active: tuple[str, int] | None = None
    for marker in _MARKER.finditer(html):
        seam_id, edge = marker.group(1), marker.group(2).lower()
        if seam_id not in declared:
            raise TemplatePolicyError(f"baseline contains unknown seam marker {seam_id}")
        if edge == "start":
            if active is not None or seam_id in ranges:
                raise TemplatePolicyError(f"baseline duplicates or nests seam {seam_id}")
            active = (seam_id, marker.end())
            continue
        if active is None or active[0] != seam_id:
            raise TemplatePolicyError(f"baseline has unmatched seam end {seam_id}")
        ranges[seam_id] = (active[1], marker.start())
        active = None
    if active is not None:
        raise TemplatePolicyError(f"baseline has unclosed seam {active[0]}")
    missing = sorted(declared - ranges.keys())
    if missing:
        raise TemplatePolicyError("baseline is missing seam markers: " + ", ".join(missing))
    return ranges


def _protected_digest(html: str, seams: tuple[SeamSpec, ...]) -> str:
    ranges = _seam_ranges(html, seams)
    protected = html
    for seam_id, (start, end) in sorted(
        ranges.items(), key=lambda item: item[1][0], reverse=True
    ):
        protected = protected[:start] + f"<pp-content:{seam_id}>" + protected[end:]
    return sha256_bytes(protected.encode())


@dataclass(frozen=True)
class BaselineSpecimen:
    slide_type: AdmittedSlideType
    html: str
    sha256: str
    protected_sha256: str

    @classmethod
    def from_html(
        cls, slide_type: AdmittedSlideType, html: str
    ) -> BaselineSpecimen:
        if "<html" not in html.lower() or "<!doctype" not in html.lower():
            raise TemplatePolicyError(
                f"baseline {slide_type.id} is not a complete HTML document"
            )
        return cls(
            slide_type=slide_type,
            html=html,
            sha256=sha256_bytes(html.encode()),
            protected_sha256=_protected_digest(html, slide_type.seams),
        )


@dataclass(frozen=True)
class SeamReplacement:
    seam_id: str
    html_fragment: str


@dataclass(frozen=True)
class SlidePatch:
    baseline_sha256: str
    replacements: tuple[SeamReplacement, ...]


_FORBIDDEN_TAGS = {
    "html",
    "head",
    "body",
    "style",
    "script",
    "link",
    "iframe",
    "object",
    "embed",
    "form",
}
_VOID_TAGS = {"br", "hr", "img", "path", "circle", "rect", "line", "polyline"}
_UNSAFE_VALUE = re.compile(r"(?:javascript:|@import|expression\s*\(|url\s*\()", re.I)


class _FragmentValidator(HTMLParser):
    def __init__(self, seam: SeamSpec) -> None:
        super().__init__(convert_charrefs=True)
        self.seam = seam
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _FORBIDDEN_TAGS or tag not in self.seam.allowed_tags:
            raise TemplatePolicyError(
                f"seam {self.seam.id} does not allow <{tag}>"
            )
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if name.startswith("on") or name in {"src", "href", "srcdoc"}:
                raise TemplatePolicyError(
                    f"seam {self.seam.id} forbids attribute {name}"
                )
            if _UNSAFE_VALUE.search(value):
                raise TemplatePolicyError(
                    f"seam {self.seam.id} contains an unsafe attribute value"
                )
            if name == "style":
                for declaration in value.split(";"):
                    if not declaration.strip():
                        continue
                    prop, separator, css_value = declaration.partition(":")
                    prop = prop.strip().lower()
                    if not separator or prop not in self.seam.allowed_style_properties:
                        raise TemplatePolicyError(
                            f"seam {self.seam.id} does not allow style {prop or declaration!r}"
                        )
                    if _UNSAFE_VALUE.search(css_value):
                        raise TemplatePolicyError(
                            f"seam {self.seam.id} contains an unsafe style value"
                        )
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if not self.stack or self.stack.pop() != tag:
            raise TemplatePolicyError(
                f"seam {self.seam.id} contains unbalanced <{tag}> markup"
            )

    def close(self) -> None:
        super().close()
        if self.stack:
            raise TemplatePolicyError(
                f"seam {self.seam.id} contains unclosed <{self.stack[-1]}> markup"
            )


def apply_seam_patch(baseline: BaselineSpecimen, patch: SlidePatch) -> str:
    """Apply exactly one replacement per declared seam to a pinned baseline."""
    if patch.baseline_sha256 != baseline.sha256:
        raise TemplatePolicyError("stale baseline digest")
    replacement_ids = [item.seam_id for item in patch.replacements]
    if len(set(replacement_ids)) != len(replacement_ids):
        raise TemplatePolicyError("patch duplicates a seam id")
    declared = {seam.id: seam for seam in baseline.slide_type.seams}
    unknown = sorted(set(replacement_ids) - declared.keys())
    missing = sorted(declared.keys() - set(replacement_ids))
    if unknown:
        raise TemplatePolicyError("patch contains unknown seams: " + ", ".join(unknown))
    if missing:
        raise TemplatePolicyError("patch is missing seams: " + ", ".join(missing))

    ranges = _seam_ranges(baseline.html, baseline.slide_type.seams)
    replacements = {item.seam_id: item.html_fragment for item in patch.replacements}
    for seam_id, fragment in replacements.items():
        if "pp:seam:" in fragment.lower():
            raise TemplatePolicyError(f"seam {seam_id} may not contain seam markers")
        validator = _FragmentValidator(declared[seam_id])
        validator.feed(fragment)
        validator.close()

    authored = baseline.html
    for seam_id, (start, end) in sorted(
        ranges.items(), key=lambda item: item[1][0], reverse=True
    ):
        authored = authored[:start] + replacements[seam_id] + authored[end:]
    if _protected_digest(authored, baseline.slide_type.seams) != baseline.protected_sha256:
        raise TemplatePolicyError("patch changed protected baseline content")
    return authored


def _parse_slide_types(value: bytes) -> tuple[AdmittedSlideType, ...]:
    try:
        data = json.loads(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TemplatePolicyError("slide-types.json is invalid JSON") from exc
    if not isinstance(data, dict):
        raise TemplatePolicyError("slide-types.json must be an object")
    items = tuple(AdmittedSlideType.from_dict(item) for item in data.get("slide_types") or [])
    if not items:
        raise TemplatePolicyError("prepared bundle has no admitted baseline specimens")
    if len({item.id for item in items}) != len(items):
        raise TemplatePolicyError("slide-types.json duplicates a slide type id")
    return items


def _validate_core_artifacts(
    artifacts: Mapping[str, bytes], slide_types: tuple[AdmittedSlideType, ...]
) -> None:
    required_files = {"template.pptx", "brand-contract.json", "slide-types.json"}
    missing = sorted(required_files - artifacts.keys())
    if missing:
        raise TemplatePolicyError("prepared bundle is missing: " + ", ".join(missing))
    for prefix in ("spec/", "base/", "clean/"):
        if not any(path.startswith(prefix) for path in artifacts):
            raise TemplatePolicyError(f"prepared bundle is missing {prefix} artifacts")
    for slide_type in slide_types:
        raw = artifacts.get(slide_type.baseline_path)
        if raw is None:
            raise TemplatePolicyError(
                f"slide type {slide_type.id} is missing {slide_type.baseline_path}"
            )
        try:
            BaselineSpecimen.from_html(slide_type, raw.decode())
        except UnicodeDecodeError as exc:
            raise TemplatePolicyError(
                f"baseline {slide_type.id} is not UTF-8"
            ) from exc
        if slide_type.preview_path and slide_type.preview_path not in artifacts:
            raise TemplatePolicyError(
                f"slide type {slide_type.id} is missing {slide_type.preview_path}"
            )
    _validate_source_artifacts(artifacts)


def _validate_source_artifacts(artifacts: Mapping[str, bytes]) -> None:
    from app.template_baselines import compile_source_baselines

    try:
        evidence = json.loads(artifacts["source-extraction.json"])
        if evidence.get("schema_version") != 2:
            raise ValueError("source-extraction.json needs complete version 2 extraction")
        if evidence.get("source_sha256") != sha256_bytes(artifacts["template.pptx"]):
            raise ValueError("source extraction belongs to different template bytes")
        count = evidence.get("slide_count")
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("source extraction has invalid slide count")
        for number in range(1, count + 1):
            for path in (f"spec/slide-{number:02d}.json", f"base/slide-{number:02d}.png", f"clean/slide-{number:02d}.png"):
                if path not in artifacts:
                    raise ValueError(f"source extraction is missing {path}")
                if evidence.get("artifacts", {}).get(path) != sha256_bytes(artifacts[path]):
                    raise ValueError(f"source extraction hash mismatch: {path}")
            record = json.loads(artifacts[f"spec/slide-{number:02d}.json"])
            if record.get("schema_version") != 2 or record.get("slide") != number:
                raise ValueError(f"unresolved or misnumbered source spec: slide-{number:02d}")
        contract = json.loads(artifacts["brand-contract.json"])
        protected = {int(n) for item in contract.get("protected", []) for n in item.get("slides", [])}
        expected = compile_source_baselines(artifacts, protected)
        for name, value in expected.items():
            if name == "slide-types.json":
                equal = json.loads(value) == json.loads(artifacts[name])
            else:
                equal = artifacts.get(name) == value
            if not equal:
                raise ValueError(f"baseline does not match source geometry: {name}")
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplatePolicyError(f"source template admission failed: {exc}") from exc


def build_prepared_manifest(
    *, source: SourceIdentity, artifacts: Mapping[str, bytes]
) -> PreparedBundleManifest:
    normalized = {_safe_path(path): value for path, value in artifacts.items()}
    slide_types = _parse_slide_types(normalized.get("slide-types.json", b""))
    _validate_core_artifacts(normalized, slide_types)
    if sha256_bytes(normalized["template.pptx"]) != source.sha256:
        raise TemplatePolicyError("source identity does not match template.pptx")
    digests = tuple(
        ArtifactDigest(path, len(value), sha256_bytes(value))
        for path, value in sorted(normalized.items())
    )
    provisional = PreparedBundleManifest(
        schema_version=1,
        bundle_id="0" * 64,
        source=source,
        artifacts=digests,
        slide_types=slide_types,
    )
    return PreparedBundleManifest(
        schema_version=1,
        bundle_id=provisional.expected_bundle_id(),
        source=source,
        artifacts=digests,
        slide_types=slide_types,
    )


def validate_prepared_manifest(
    manifest_value: object,
    artifacts: Mapping[str, bytes],
    *,
    expected_source_uri: str | None = None,
    expected_generation: str | None = None,
    expected_etag: str | None = None,
) -> PreparedBundleManifest:
    manifest = PreparedBundleManifest.from_dict(manifest_value)
    if manifest.bundle_id != manifest.expected_bundle_id():
        raise TemplatePolicyError("prepared bundle identity digest mismatch")
    if expected_source_uri and manifest.source.gs_uri != expected_source_uri:
        raise TemplatePolicyError("prepared bundle belongs to a different source template")
    if expected_generation and manifest.source.generation != str(expected_generation):
        raise TemplatePolicyError("prepared bundle source generation is stale")
    if expected_etag and manifest.source.etag != str(expected_etag):
        raise TemplatePolicyError("prepared bundle source etag is stale")

    normalized = {_safe_path(path): value for path, value in artifacts.items()}
    declared = {item.path: item for item in manifest.artifacts}
    missing = sorted(declared.keys() - normalized.keys())
    if missing:
        raise TemplatePolicyError("prepared bundle is missing: " + ", ".join(missing))
    for path, digest in declared.items():
        value = normalized[path]
        if len(value) != digest.size_bytes or sha256_bytes(value) != digest.sha256:
            raise TemplatePolicyError(f"prepared artifact hash mismatch: {path}")
    if sha256_bytes(normalized.get("template.pptx", b"")) != manifest.source.sha256:
        raise TemplatePolicyError("prepared template bytes do not match source identity")
    slide_types = _parse_slide_types(normalized.get("slide-types.json", b""))
    _validate_core_artifacts(normalized, slide_types)
    if slide_types != manifest.slide_types:
        raise TemplatePolicyError("manifest slide types do not match slide-types.json")
    return manifest
