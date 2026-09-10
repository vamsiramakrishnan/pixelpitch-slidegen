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

"""Thread-safe progress side channel from synchronous tools to A2A streaming."""

from __future__ import annotations

import itertools
import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressUpdate:
    sequence: int
    stage: str
    title: str
    detail: str
    stage_index: int
    stage_total: int
    current: int | None = None
    total: int | None = None
    slide_index: int | None = None
    slide_title: str | None = None
    # Optional engagement fragment (e.g. the dino runner) rendered beside
    # the progress copy. The token resolves to cached HTML at the A2A edge.
    fragment_token: str | None = None


_LOCK = threading.Lock()
_SEQUENCE = itertools.count(1)
# Monotonic UX-milestone percentages per context: GE's Thinking panel must
# never regress between snapshots, so each context remembers its high-water
# mark and every computed pct is clamped up to it.
_CONTEXT_PCT: dict[str, int] = {}


def display_percent(context_key: str | None, update: ProgressUpdate) -> int:
    """UX-milestone percentage for one update, clamped monotonically.

    Milestones are feel, not measurement: brief 5-15, authoring 20-70
    (interpolated by current/total when known), conversion 75, quality 85,
    delivery 95-100.
    """
    if update.stage_index <= 1:
        pct = 5 if update.stage == "intake" else 10
    elif update.stage_index == 2:
        base, span = 20, 50
        if update.total and update.current is not None and update.total > 0:
            pct = base + int(span * min(1.0, update.current / update.total))
        else:
            pct = base + 10
    elif update.stage_index == 3:
        pct = 75
    elif update.stage_index == 4:
        pct = 85
    else:
        pct = 100
    with _LOCK:
        key = context_key or ""
        pct = max(pct, _CONTEXT_PCT.get(key, 0))
        _CONTEXT_PCT[key] = pct
    return pct
_UPDATES: dict[str, deque[ProgressUpdate]] = {}
_CONTEXT_SESSION_KEYS: dict[str, str] = {}
_MAX_UPDATES_PER_SESSION = 64


def _session_key(tool_context) -> str | None:
    session = getattr(tool_context, "session", None)
    session_id = getattr(session, "id", None)
    return f"session:{session_id}" if session_id else None


def bind_context(context_id: str, session_id: str) -> None:
    """Bind an A2A context to the ADK session seen by tool contexts."""
    with _LOCK:
        _CONTEXT_SESSION_KEYS[context_id] = f"session:{session_id}"


def report_progress(
    tool_context,
    *,
    stage: str,
    title: str,
    detail: str,
    stage_index: int,
    stage_total: int = 5,
    current: int | None = None,
    total: int | None = None,
    slide_index: int | None = None,
    slide_title: str | None = None,
    fragment_token: str | None = None,
) -> None:
    """Publish one real pipeline milestone for an ADK session."""
    key = _session_key(tool_context)
    if key is None:
        return
    update = ProgressUpdate(
        sequence=next(_SEQUENCE),
        stage=stage,
        title=title,
        detail=detail,
        stage_index=stage_index,
        stage_total=stage_total,
        current=current,
        total=total,
        slide_index=slide_index,
        slide_title=slide_title,
        fragment_token=fragment_token,
    )
    with _LOCK:
        queue = _UPDATES.setdefault(key, deque(maxlen=_MAX_UPDATES_PER_SESSION))
        queue.append(update)


def get_context_progress(context_id: str | None) -> ProgressUpdate | None:
    """Return the latest update, retained for diagnostic callers."""
    if not context_id:
        return None
    with _LOCK:
        key = _CONTEXT_SESSION_KEYS.get(context_id)
        queue = _UPDATES.get(key) if key else None
        return queue[-1] if queue else None


def get_context_progress_updates(
    context_id: str | None, after_sequence: int = 0
) -> list[ProgressUpdate]:
    """Return every retained update newer than ``after_sequence`` in order."""
    if not context_id:
        return []
    with _LOCK:
        key = _CONTEXT_SESSION_KEYS.get(context_id)
        queue = _UPDATES.get(key) if key else None
        if not queue:
            return []
        return [update for update in queue if update.sequence > after_sequence]


def get_session_progress_updates(
    session_id: str | None, after_sequence: int = 0
) -> list[ProgressUpdate]:
    """Return updates for an ADK session without requiring an A2A context."""
    if not session_id:
        return []
    with _LOCK:
        queue = _UPDATES.get(f"session:{session_id}")
        if not queue:
            return []
        return [update for update in queue if update.sequence > after_sequence]


def clear_session_progress(session_id: str | None) -> None:
    """Discard retained updates when a native ADK turn has consumed them."""
    if not session_id:
        return
    with _LOCK:
        _UPDATES.pop(f"session:{session_id}", None)


def session_key_for_context(context_id: str | None) -> str | None:
    """Session key bound to an A2A context, for fragment resolution."""
    with _LOCK:
        return _CONTEXT_SESSION_KEYS.get(context_id or "")


def clear_context(context_id: str | None) -> None:
    if not context_id:
        return
    with _LOCK:
        key = _CONTEXT_SESSION_KEYS.pop(context_id, None)
        if key:
            _UPDATES.pop(key, None)
