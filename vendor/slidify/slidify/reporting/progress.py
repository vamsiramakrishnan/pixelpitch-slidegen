from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class ProgressEvent:
    """Structured status event for long-running slidify commands."""

    event: str
    stage: str
    message: str
    status: str = "info"
    current: int | None = None
    total: int | None = None
    path: str | None = None
    elapsed_seconds: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None and v != {}}


class ProgressReporter:
    """Emit progress as plain text, JSONL, both, or nothing."""

    def __init__(
        self,
        *,
        mode: str = "plain",
        stream: TextIO | None = None,
        jsonl_path: Path | None = None,
    ) -> None:
        self.mode = mode
        self.stream = stream or sys.stderr
        self._jsonl_file: TextIO | None = None
        if jsonl_path is not None:
            jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl_file = jsonl_path.open("w", encoding="utf-8")

    def close(self) -> None:
        if self._jsonl_file is not None:
            self._jsonl_file.close()
            self._jsonl_file = None

    def emit(self, event: ProgressEvent | dict[str, Any], **extra: Any) -> None:
        payload = event.to_dict() if isinstance(event, ProgressEvent) else dict(event)
        payload.update({k: v for k, v in extra.items() if v is not None})
        payload.setdefault("status", "info")
        payload.setdefault(
            "timestamp", datetime.now(UTC).isoformat(timespec="seconds")
        )
        if self.mode == "jsonl":
            self._write_jsonl(payload, self.stream)
        elif self.mode == "plain":
            self._write_plain(payload)
        elif self.mode == "both":
            self._write_plain(payload)
            self._write_jsonl(payload, self.stream)
        elif self.mode != "off":
            raise ValueError(f"unknown progress mode: {self.mode}")
        if self._jsonl_file is not None:
            self._write_jsonl(payload, self._jsonl_file)

    def _write_plain(self, payload: dict[str, Any]) -> None:
        prefix = payload.get("stage", "progress")
        message = payload.get("message", payload.get("event", "status"))
        current = payload.get("current")
        total = payload.get("total")
        if current is not None and total is not None:
            line = f"[{prefix}] {current}/{total} {message}"
        else:
            line = f"[{prefix}] {message}"
        print(line, file=self.stream, flush=True)

    @staticmethod
    def _write_jsonl(payload: dict[str, Any], stream: TextIO) -> None:
        print(json.dumps(payload, default=str, sort_keys=True), file=stream, flush=True)


def progress_callback(reporter: ProgressReporter | None) -> ProgressCallback | None:
    if reporter is None:
        return None
    return reporter.emit


def emit_progress(callback: ProgressCallback | None, **payload: Any) -> None:
    """Call a progress callback defensively.

    Progress reporting should never be able to break a conversion or harvest.
    """
    if callback is None:
        return
    try:
        callback(ProgressEvent(**payload).to_dict())
    except Exception:
        return
