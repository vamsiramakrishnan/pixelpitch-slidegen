"""Generation-pinned template identities and immutable bundle publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from google.api_core.exceptions import NotFound, PreconditionFailed

from app.template_policy import PreparedBundleManifest, validate_prepared_manifest


@dataclass(frozen=True)
class TemplateVersion:
    bucket: str
    name: str
    generation: str

    def __post_init__(self):
        if not self.bucket or "/" in self.bucket:
            raise ValueError("invalid template bucket")
        if not self.name or self.name.startswith("/") or ".." in self.name.split("/"):
            raise ValueError("invalid template object name")
        if not re.fullmatch(r"[1-9][0-9]*", self.generation):
            raise ValueError("invalid template generation")

    @classmethod
    def from_dict(cls, value: dict) -> TemplateVersion:
        return cls(str(value.get("bucket") or ""), str(value.get("name") or ""), str(value.get("generation") or ""))

    @classmethod
    def from_uri(cls, uri: str, generation: str) -> TemplateVersion:
        if not uri.startswith("gs://"):
            raise ValueError("template source must be a gs:// URI")
        bucket, _, name = uri[5:].partition("/")
        return cls(bucket, name, str(generation))

    def to_dict(self) -> dict:
        return {"bucket": self.bucket, "name": self.name, "generation": self.generation}

    @property
    def uri(self) -> str:
        return f"gs://{self.bucket}/{self.name}"

    @property
    def source_id(self) -> str:
        return hashlib.sha256(self.uri.encode()).hexdigest()

    @property
    def task_id(self) -> str:
        return "template-" + hashlib.sha256(f"{self.uri}\n{self.generation}".encode()).hexdigest()

    @property
    def prefix(self) -> str:
        return f"prepared-templates/{self.source_id}/{self.generation}/"

    @property
    def status_path(self) -> str:
        return f"template-jobs/{self.task_id}.json"

    def is_source(self, bucket: str, prefix: str = "templates/") -> bool:
        relative = self.name.removeprefix(prefix)
        return self.bucket == bucket and self.name.startswith(prefix) and "/" not in relative and relative.lower().endswith(".pptx")


def ready_prefix(bucket, version: TemplateVersion) -> str | None:
    """A ready marker selects one complete bundle for exactly one generation."""
    marker = bucket.blob(version.prefix + "ready.json")
    if not marker.exists():
        return None
    try:
        ready = json.loads(marker.download_as_bytes())
    except NotFound:
        return None
    bundle_id = ready.get("bundle_id", "")
    if not isinstance(bundle_id, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_id):
        raise ValueError("invalid template ready marker")
    return version.prefix + bundle_id + "/"


def _create_immutable(blob, content: bytes) -> None:
    try:
        blob.upload_from_string(content, if_generation_match=0)
    except PreconditionFailed:
        if blob.download_as_bytes() != content:
            raise ValueError(f"immutable template artifact collision: {blob.name}") from None


def publish_bundle(bucket, version: TemplateVersion, manifest: PreparedBundleManifest, artifacts: dict[str, bytes]) -> str:
    validate_prepared_manifest(manifest.to_dict(), artifacts, expected_source_uri=version.uri, expected_generation=version.generation)
    prefix = version.prefix + manifest.bundle_id + "/"
    for name, content in artifacts.items():
        _create_immutable(bucket.blob(prefix + name), content)
    _create_immutable(bucket.blob(prefix + "manifest.json"), manifest.to_json().encode())
    # Concurrent attempts may produce different, valid catalogs for the same
    # source generation. The first complete bundle wins; neither mutates it.
    try:
        bucket.blob(version.prefix + "ready.json").upload_from_string(
            json.dumps({"bundle_id": manifest.bundle_id}).encode(),
            content_type="application/json", if_generation_match=0,
        )
    except PreconditionFailed:
        pass
    committed = ready_prefix(bucket, version)
    if committed is None:
        raise RuntimeError("template publication produced no ready marker")
    return committed


def is_current(client, version: TemplateVersion) -> bool:
    try:
        source = client.bucket(version.bucket).blob(version.name)
        source.reload()
    except NotFound:
        return False
    return str(source.generation) == version.generation
