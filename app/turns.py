# Copyright 2026 Google LLC

"""Canonical boundary envelope for A2UI actions entering the ADK runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ACTION_PREFIX = "PIXELPITCH_ACTION_V1:"


@dataclass(frozen=True)
class ActionTurn:
    name: str
    context: dict[str, Any]


def encode_action_turn(name: str, context: dict[str, Any] | None = None) -> str:
    payload = {"name": str(name), "context": context or {}}
    return ACTION_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_action_turn(text: str) -> ActionTurn | None:
    """Decode the last canonical action envelope in a text message."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith(ACTION_PREFIX):
            continue
        try:
            payload = json.loads(line[len(ACTION_PREFIX) :])
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            return None
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            return None
        return ActionTurn(name=payload["name"], context=context)
    return None
