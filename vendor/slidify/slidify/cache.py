"""Structural cache for classification decisions."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path

import structlog

from slidify.models import Decision, VisualUnit

log = structlog.get_logger(__name__)


def structural_hash(unit: VisualUnit) -> str:
    """Construct a structural hash for a VisualUnit.

    The hash is the recursive shape signature defined in
    `slidify.patterns.signatures` — it captures anchor kind, normalized
    class set, quantized bbox, and child signatures.
    """
    # Lazy import to avoid circular dep at module load.
    from slidify.patterns.signatures import signature_hash

    return signature_hash(unit)


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> Decision | None: ...

    @abstractmethod
    def put(self, key: str, decision: Decision) -> None: ...


class MemoryCache(CacheBackend):
    def __init__(self, max_entries: int = 10_000) -> None:
        self._max = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[str, Decision] = OrderedDict()

    def get(self, key: str) -> Decision | None:
        with self._lock:
            d = self._data.get(key)
            if d is not None:
                self._data.move_to_end(key)
            return d

    def put(self, key: str, decision: Decision) -> None:
        with self._lock:
            self._data[key] = decision
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


class DiskCache(CacheBackend):
    def __init__(self, directory: str | Path, ttl: int | None = None) -> None:
        try:
            import diskcache
        except ImportError as e:
            raise RuntimeError("diskcache not installed") from e
        self._dc = diskcache.Cache(str(directory))
        self._ttl = ttl

    def get(self, key: str) -> Decision | None:
        raw = self._dc.get(key)
        if raw is None:
            return None
        try:
            return Decision.model_validate_json(raw)
        except Exception:
            return None

    def put(self, key: str, decision: Decision) -> None:
        self._dc.set(key, decision.model_dump_json(), expire=self._ttl)


class StructuralCache:
    """Wraps a backend with hit-rate accounting."""

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self.backend = backend or MemoryCache()
        self.hits = 0
        self.misses = 0

    def get(self, unit: VisualUnit) -> Decision | None:
        key = structural_hash(unit)
        v = self.backend.get(key)
        if v is None:
            self.misses += 1
        else:
            self.hits += 1
        return v

    def put(self, unit: VisualUnit, decision: Decision) -> None:
        self.backend.put(structural_hash(unit), decision)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total
