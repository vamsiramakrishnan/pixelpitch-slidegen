"""Tests for slidify.classifier.llm provider factory.

These don't make real API calls — they verify the factory dispatches correctly
and that backends raise informative errors when env vars are missing.
"""

from __future__ import annotations

import pytest

from slidify.classifier.llm import (
    DEFAULT_MODELS,
    auto_select_backend,
    build_provider,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "SLIDIFY_PREFER_VERTEX_GEMINI",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        build_provider("not-a-backend")


def test_default_models_present():
    for backend in (
        "gemini-aistudio",
        "gemini-vertex",
        "anthropic",
        "claude-vertex",
    ):
        assert backend in DEFAULT_MODELS


def test_anthropic_requires_key(monkeypatch):
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_provider("anthropic")


def test_gemini_aistudio_requires_key(monkeypatch):
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_provider("gemini-aistudio")


def test_claude_vertex_requires_project(monkeypatch):
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        build_provider("claude-vertex")


def test_gemini_vertex_requires_project(monkeypatch):
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        build_provider("gemini-vertex")


def test_auto_select_with_no_env():
    assert auto_select_backend() is None


def test_auto_select_prefers_gemini_aistudio(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert auto_select_backend() == "gemini-aistudio"


def test_auto_select_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert auto_select_backend() == "anthropic"


def test_auto_select_falls_back_to_claude_vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "myproj")
    assert auto_select_backend() == "claude-vertex"


def test_auto_select_prefers_gemini_vertex_when_flagged(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "myproj")
    monkeypatch.setenv("SLIDIFY_PREFER_VERTEX_GEMINI", "1")
    assert auto_select_backend() == "gemini-vertex"
