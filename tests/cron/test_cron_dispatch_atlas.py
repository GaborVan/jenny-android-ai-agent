"""Instradamento del job ``atlas`` nel ``CronDispatcher``.

Il dispatcher non deve contenere logica Atlas: il suo unico compito è chiamare
``run_atlas``. Se un giorno qualcuno reimplementa il run qui dentro — come è
successo a Dream, che oggi vive in due copie — questo test resta verde ma il
prossimo cambiamento andrà fatto in due posti. Meglio fissarlo adesso.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_ATLAS_JOB = SimpleNamespace(name="atlas", id="job-atlas")


class _FakeAgent:
    def __init__(self, sessions_dir: Path) -> None:
        self.context = SimpleNamespace(memory=None, timezone=None)
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self.prompts: list[str] = []

    async def process_direct(self, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


def _dispatcher(agent) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=Config(),
        cron=MagicMock(),
        get_message_tool=lambda: None,
        deliver_to_channel=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_atlas_job_reaches_run_atlas(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    async def _fake_run_atlas(agent, *, store=None, force=False):
        from jenny.agent.atlas import AtlasOutcome

        seen["agent"] = agent
        seen["store"] = store
        seen["force"] = force
        return AtlasOutcome(status="written", elapsed=0.1)

    monkeypatch.setattr("jenny.agent.atlas.run_atlas", _fake_run_atlas)
    agent = _FakeAgent(tmp_path)

    result = await _dispatcher(agent).dispatch(_ATLAS_JOB)

    assert result is None  # Atlas non consegna niente all'utente
    assert seen["agent"] is agent
    assert seen["force"] is False
    assert seen["store"] is not None


@pytest.mark.asyncio
async def test_atlas_store_is_built_from_the_dispatcher_config(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_run_atlas(agent, *, store=None, force=False):
        from jenny.agent.atlas import AtlasOutcome

        captured["wikis_dir"] = store.wikis_dir
        captured["default_wiki"] = store.default_wiki
        return AtlasOutcome(status="skipped_no_wikis")

    monkeypatch.setattr("jenny.agent.atlas.run_atlas", _fake_run_atlas)
    config = Config()

    await _dispatcher(_FakeAgent(tmp_path)).dispatch(_ATLAS_JOB)

    assert captured["wikis_dir"] == config.workspace_path / config.wiki.wikis_dir
    assert captured["default_wiki"] == config.wiki.default_wiki


@pytest.mark.asyncio
async def test_a_run_without_wikis_is_not_an_error(tmp_path):
    """Nessuna wiki è lo stato normale di un workspace nuovo, non un guasto."""
    agent = _FakeAgent(tmp_path)

    result = await _dispatcher(agent).dispatch(_ATLAS_JOB)

    assert result is None
    assert agent.prompts == []
