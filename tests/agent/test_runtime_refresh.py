from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from jenny.agent.loop import AgentLoop
from jenny.bus.queue import MessageBus


async def test_llm_runtime_returns_provider(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=123)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=1000,
    )

    runtime = await loop.llm_runtime()
    assert runtime.provider is provider
