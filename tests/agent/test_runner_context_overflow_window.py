"""Recovery dell'overflow di contesto quando la finestra non è dichiarata.

``AgentRunSpec.context_window_tokens`` è ``int | None``, ma il recovery faceva
aritmetica sul valore senza controllarlo: con la finestra assente sollevava
``TypeError`` *dentro* il ramo di recupero, trasformando un overflow recuperabile
in un errore di tipo. Gli spec costruiti direttamente — come quelli di questi
test — omettono spesso il campo, quindi il caso era raggiungibile e non coperto.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jenny.config.schema import AgentDefaults
from jenny.providers.base import LLMProvider, LLMResponse

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


def _overflow(message: str) -> LLMResponse:
    return LLMResponse(
        content=f"Error: {message}",
        finish_reason="error",
        error_code="context_length_exceeded",
    )


def _tools():
    tools = MagicMock()
    tools.get_definitions.return_value = []
    return tools


def _spec(**overrides):
    from jenny.agent.runner import AgentRunSpec

    kwargs = dict(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=_tools(),
        model="test-model",
        max_iterations=4,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )
    kwargs.update(overrides)
    return AgentRunSpec(**kwargs)


@pytest.mark.asyncio
async def test_no_declared_window_gives_up_without_crashing():
    """Senza finestra e senza limite nell'errore non c'è niente da dimezzare.

    Prima: ``TypeError`` dentro il recovery. Ora: si arrende e lascia emergere
    l'errore del provider, che almeno dice cos'è successo.
    """
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        return _overflow("context_length_exceeded")

    provider.chat_with_retry = chat_with_retry
    spec = _spec()
    assert spec.context_window_tokens is None

    result = await AgentRunner(provider).run(spec)

    assert result.stop_reason == "error"
    # Un solo tentativo: senza leva non si ritenta a vuoto.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_no_declared_window_uses_the_limit_from_the_error():
    """Con la finestra ignota, un limite dichiarato dal provider è la sola
    informazione utile disponibile: va usato invece di arrendersi."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _overflow("This model's maximum context length is 8192 tokens")
        return LLMResponse(content="recovered", usage={"completion_tokens": 5})

    provider.chat_with_retry = chat_with_retry
    spec = _spec()

    result = await AgentRunner(provider).run(spec)

    assert result.final_content == "recovered"
    assert spec.context_window_tokens == 8192


@pytest.mark.asyncio
async def test_declared_window_is_still_halved():
    """Non-regressione: col campo valorizzato l'euristica resta quella."""
    from jenny.agent.runner import AgentRunner

    provider = MagicMock(spec=LLMProvider)
    calls = {"n": 0}

    async def chat_with_retry(*, messages, tools=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _overflow("context_length_exceeded")
        return LLMResponse(content="recovered", usage={"completion_tokens": 5})

    provider.chat_with_retry = chat_with_retry
    spec = _spec(context_window_tokens=65536)

    result = await AgentRunner(provider).run(spec)

    assert result.final_content == "recovered"
    assert spec.context_window_tokens == 32768
