"""Shared fixtures and helpers for agent tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jenny.agent.loop import AgentLoop
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider


def make_provider(
    default_model: str = "test-model",
    *,
    max_tokens: int = 4096,
    spec: bool = True,
) -> MagicMock:
    """Create a spec-limited LLM provider mock."""
    mock_type = MagicMock(spec=LLMProvider) if spec else MagicMock()
    provider = mock_type
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    if not spec:
        # Optional provider hook (see estimate_prompt_tokens_chain); the
        # LLMProvider ABC does not define it, so spec-limited mocks omit it
        # and the runtime falls back to the character heuristic.
        provider.estimate_prompt_tokens.return_value = (10_000, "test")
    return provider


def make_loop(
    tmp_path: Path,
    *,
    model: str = "test-model",
    context_window_tokens: int = 128_000,
    session_ttl_minutes: int = 0,
    max_messages: int = 120,
    tools_config=None,
    model_presets: dict | None = None,
    initial_model_preset: str | None = None,
    hooks: list | None = None,
    provider: MagicMock | None = None,
    patch_deps: bool = False,
) -> AgentLoop:
    """Create a real AgentLoop for testing.

    Args:
        patch_deps: If True, patch ContextBuilder/SessionManager/SubagentManager
                    during construction (needed when workspace has no real files).
    """
    bus = MessageBus()
    if provider is None:
        provider = make_provider(default_model=model)

    kwargs = dict(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model=model,
        context_window_tokens=context_window_tokens,
        session_ttl_minutes=session_ttl_minutes,
        max_messages=max_messages,
    )
    if tools_config is not None:
        kwargs["tools_config"] = tools_config
    if model_presets is not None:
        kwargs["model_presets_config"] = model_presets
    if initial_model_preset is not None:
        kwargs["initial_model_preset"] = initial_model_preset
    if hooks is not None:
        kwargs["hooks"] = hooks

    if patch_deps:
        with patch("jenny.agent.loop.ContextBuilder"), \
             patch("jenny.agent.loop.SessionManager"), \
             patch("jenny.agent.loop.SubagentManager") as mock_sub_mgr:
            mock_sub_mgr.return_value.cancel_by_session = AsyncMock(return_value=0)
            return AgentLoop(**kwargs)
    return AgentLoop(**kwargs)


@pytest.fixture
def loop_factory(tmp_path):
    """Fixture providing a factory for creating AgentLoop instances."""
    def _factory(**kwargs):
        return make_loop(tmp_path, **kwargs)
    return _factory
