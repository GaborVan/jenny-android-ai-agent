"""Instradamento del job ``gardener`` nel ``CronDispatcher``.

Il dispatcher non deve contenere logica del giardiniere: sceglie il progetto con
``pick_project`` e chiama ``run_gardener``. Stesso motivo del gemello Atlas — se
un giorno qualcuno reimplementa qui la selezione, questo test resta verde ma il
prossimo cambiamento andrà fatto in due posti.

Il test che vale più degli altri è quello su ``enabled``:
``register_system_job`` non ha una controparte che deregistri, quindi un job
registrato da un avvio precedente **resta nello store del cron** anche dopo che
la sezione è stata spenta. Se il controllo vivesse solo alla registrazione,
spegnere il giardiniere non lo spegnerebbe.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.config.schema import Config
from jenny.runtime.cron_dispatch import CronDispatcher

_JOB = SimpleNamespace(name="gardener", id="job-gardener")


class _FakeAgent:
    def __init__(self, sessions_dir: Path, *, active: tuple[str, ...] = ()) -> None:
        self.context = SimpleNamespace(memory=None, timezone=None)
        self.sessions = SimpleNamespace(
            sessions_dir=sessions_dir,
            read_session_metadata=lambda key: None,
        )
        self._active = active

    def active_session_keys(self) -> tuple[str, ...]:
        return self._active

    async def process_direct(self, prompt: str, **_kwargs):
        return SimpleNamespace(metadata={"_stop_reason": "completed"}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        pass


def _dispatcher(agent, config: Config | None = None) -> CronDispatcher:
    return CronDispatcher(
        get_agent=lambda: agent,
        config=config or Config(),
        cron=MagicMock(),
        heartbeat_cfg=SimpleNamespace(),
    )


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """``Config.workspace_path`` viene dal contesto runtime, non dal campo.

    Non è un dettaglio da test: è la stessa strada che ``_run_atlas`` percorre
    (``AtlasStore.from_config(self._config.workspace_path, ...)``), quindi
    spostare il workspace qui prova il dispatcher nelle condizioni vere.
    """
    from jenny.config import paths

    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    yield
    paths.set_workspace_dir(str(previous))


def _config(**knobs) -> Config:
    config = Config()
    for key, value in knobs.items():
        setattr(config.agents.defaults.gardener, key, value)
    return config


def _project(tmp_path: Path, name: str = "viaggio") -> Path:
    root = tmp_path / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "raw" / "journal").mkdir(parents=True)
    (root / "raw" / "journal" / "20260823.md").write_text("- 09:00 — x\n", encoding="utf-8")
    return root


@pytest.mark.asyncio
async def test_the_job_reaches_run_gardener_with_the_picked_project(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    async def _fake_run(agent, store):
        from jenny.agent.gardener import GardenerOutcome

        seen["agent"] = agent
        seen["project"] = store.name
        return GardenerOutcome(status="written", elapsed=0.1, lines=2, writes=1)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)

    _project(tmp_path)

    agent = _FakeAgent(tmp_path)
    await _dispatcher(agent, _config())._dispatch(_JOB)

    assert seen["project"] == "viaggio"
    assert seen["agent"] is agent


@pytest.mark.asyncio
async def test_a_disabled_gardener_does_not_run_even_if_the_job_fires(tmp_path, monkeypatch):
    called: list[str] = []

    async def _fake_run(agent, store):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    _project(tmp_path)

    await _dispatcher(_FakeAgent(tmp_path), _config(enabled=False))._dispatch(_JOB)

    assert called == []


@pytest.mark.asyncio
async def test_no_project_ready_means_no_call(tmp_path, monkeypatch):
    called: list[str] = []

    async def _fake_run(agent, store):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    (tmp_path / "wikis").mkdir()

    await _dispatcher(_FakeAgent(tmp_path), _config())._dispatch(_JOB)

    assert called == []


@pytest.mark.asyncio
async def test_the_in_flight_sessions_come_from_the_agent(tmp_path, monkeypatch):
    """Il dispatcher deve *chiedere* all'agente chi sta lavorando adesso: se non
    lo passasse, il cancello più importante sarebbe scavalcato da chi lo chiama.
    """
    called: list[str] = []

    async def _fake_run(agent, store):
        called.append(store.name)

    monkeypatch.setattr("jenny.agent.gardener.run_gardener", _fake_run)
    _project(tmp_path)

    await _dispatcher(
        _FakeAgent(tmp_path, active=("project:viaggio",)), _config()
    )._dispatch(_JOB)

    assert called == []
