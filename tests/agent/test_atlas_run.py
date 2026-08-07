"""Contratto di ``run_atlas``.

Due invarianti:

- Il provider viene chiamato solo quando serve: niente wiki o wiki invariata ⇒
  zero turni.
- Il fingerprint avanza solo quando il run ha davvero prodotto qualcosa. Se
  avanzasse dopo una scrittura bloccata, l'aggiornamento andrebbe perso fino
  alla prossima modifica della wiki.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.atlas import AtlasStore, run_atlas
from jenny.agent.tools.file_state import FileStates

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")


class _FakeAgent:
    """Agente minimale: registra le chiamate e restituisce un esito pilotato."""

    def __init__(self, sessions_dir: Path, *, stop_reason: str = "completed") -> None:
        self.context = SimpleNamespace(memory=None, timezone="Europe/Rome")
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self.calls: list[dict] = []
        self._stop_reason = stop_reason
        self.evicted: list[str] = []

    async def process_direct(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(metadata={"_stop_reason": self._stop_reason}, usage={})

    def evict_pruned_sessions(self, keys) -> None:
        self.evicted.extend(keys)


class _ExplodingAgent(_FakeAgent):
    async def process_direct(self, prompt: str, **kwargs):
        raise RuntimeError("provider is down")


def _store_with_wiki(tmp_path: Path) -> AtlasStore:
    (tmp_path / "memory").mkdir(exist_ok=True)
    root = tmp_path / "wikis" / "main"
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# main\n", encoding="utf-8")
    (root / "wiki" / "entities" / "ada.md").write_text("# Ada\n", encoding="utf-8")
    return AtlasStore(tmp_path)


def _states(*, attempted: int, ok: int) -> FileStates:
    states = FileStates()
    for _ in range(attempted):
        states.record_write_attempt()
    for _ in range(ok):
        states.record_write(Path("/tmp/whatever"))
    return states


def _with_states(store: AtlasStore, states: FileStates) -> AtlasStore:
    """Sostituisce il registry del run con uno che espone *states*."""
    store.build_tools = lambda: SimpleNamespace(file_states=states)  # type: ignore[method-assign]
    return store


class TestSkips:
    @pytest.mark.asyncio
    async def test_no_wikis_means_no_provider_call(self, tmp_path):
        (tmp_path / "memory").mkdir()
        agent = _FakeAgent(tmp_path)

        outcome = await run_atlas(agent, store=AtlasStore(tmp_path))

        assert outcome.status == "skipped_no_wikis"
        assert agent.calls == []

    @pytest.mark.asyncio
    async def test_unchanged_wiki_means_no_provider_call(self, tmp_path):
        store = _store_with_wiki(tmp_path)
        store.write_state(store.fingerprint())
        agent = _FakeAgent(tmp_path)

        outcome = await run_atlas(agent, store=store)

        assert outcome.status == "skipped_unchanged"
        assert agent.calls == []

    @pytest.mark.asyncio
    async def test_force_overrides_the_fingerprint_check(self, tmp_path):
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=1, ok=1))
        store.write_state(store.fingerprint())
        agent = _FakeAgent(tmp_path)

        outcome = await run_atlas(agent, store=store, force=True)

        assert outcome.status == "written"
        assert len(agent.calls) == 1

    @pytest.mark.asyncio
    async def test_force_does_not_override_the_missing_wiki_check(self, tmp_path):
        (tmp_path / "memory").mkdir()
        agent = _FakeAgent(tmp_path)

        outcome = await run_atlas(agent, store=AtlasStore(tmp_path), force=True)

        assert outcome.status == "skipped_no_wikis"
        assert agent.calls == []


class TestFingerprintAdvance:
    @pytest.mark.asyncio
    async def test_advances_after_a_successful_write(self, tmp_path):
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=1, ok=1))
        expected = store.fingerprint()

        outcome = await run_atlas(_FakeAgent(tmp_path), store=store)

        assert outcome.status == "written"
        assert store.last_fingerprint() == expected

    @pytest.mark.asyncio
    async def test_advances_when_nothing_needed_changing(self, tmp_path):
        """Zero tentativi di scrittura su un run pulito = "va già bene così"."""
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=0, ok=0))

        outcome = await run_atlas(_FakeAgent(tmp_path), store=store)

        assert outcome.status == "written"
        assert store.last_fingerprint() != ""

    @pytest.mark.asyncio
    async def test_does_not_advance_when_every_write_was_blocked(self, tmp_path):
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=2, ok=0))

        outcome = await run_atlas(_FakeAgent(tmp_path), store=store)

        assert outcome.status == "no_write"
        assert store.last_fingerprint() == ""

    @pytest.mark.asyncio
    async def test_does_not_advance_when_the_turn_was_cut_short(self, tmp_path):
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=1, ok=1))
        agent = _FakeAgent(tmp_path, stop_reason="max_iterations")

        outcome = await run_atlas(agent, store=store)

        assert outcome.status == "incomplete"
        assert store.last_fingerprint() == ""

    @pytest.mark.asyncio
    async def test_does_not_advance_when_the_provider_raises(self, tmp_path):
        store = _store_with_wiki(tmp_path)

        outcome = await run_atlas(_ExplodingAgent(tmp_path), store=store)

        assert outcome.status == "failed"
        assert "provider is down" in outcome.detail
        assert store.last_fingerprint() == ""


class TestTurnShape:
    @pytest.mark.asyncio
    async def test_runs_ephemeral_on_an_internal_session_with_its_own_tools(self, tmp_path):
        states = _states(attempted=1, ok=1)
        store = _with_states(_store_with_wiki(tmp_path), states)
        agent = _FakeAgent(tmp_path)

        await run_atlas(agent, store=store)

        call = agent.calls[0]
        assert call["ephemeral"] is True
        assert call["session_key"].startswith("atlas:")
        assert call["tools"].file_states is states

    @pytest.mark.asyncio
    async def test_prompt_carries_the_inventory(self, tmp_path):
        store = _with_states(_store_with_wiki(tmp_path), _states(attempted=1, ok=1))
        agent = _FakeAgent(tmp_path)

        await run_atlas(agent, store=store)

        assert "## Wiki Inventory" in agent.calls[0]["prompt"]
        assert "Ada" in agent.calls[0]["prompt"]
