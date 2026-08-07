"""Cio che il prompt dichiara e cio che il modello riceve devono coincidere.

Il difetto che questi test bloccano: l'inventario generato in coda al system
prompt leggeva il registry *di default* del loop, mentre il runner usa quello
del turno. Con un registry sostituito — Dream, Atlas — il prompt descriveva un
agente diverso da quello che girava: annunciava ``spawn`` e ``cron``, che non
ha, e taceva su ``apply_patch``, che e l'unico modo con cui puo scrivere.

La garanzia qui NON e una lista di casi. E la *derivazione*: si prova che
l'inventario nasce dal registry del turno, con un registry inventato che non
somiglia a nessuno di quelli veri. Se vale per quello, vale per ogni registry
che esistera mai — Atlas compreso, senza che nessuno debba aggiungerlo qui.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.agent.tools.base import Tool
from jenny.agent.tools.registry import ToolRegistry
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMResponse

_INVENTORY_HEADING = "# The tools you actually have"


class _NamedTool(Tool):
    """Tool minimo con un nome arbitrario: serve solo a comparire in un registry."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"test tool {self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **_kwargs: Any) -> str:
        return "ok"


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_NamedTool(name))
    return registry


_LISTED_AFTER = "so it is true right now:"


def _inventory_names(system_prompt: str) -> set[str]:
    """I nomi elencati nel blocco inventario, letti dal prompt come lo vede il modello."""
    assert _INVENTORY_HEADING in system_prompt, "inventario assente dal system prompt"
    block = system_prompt.rsplit(_INVENTORY_HEADING, 1)[1]
    listed = block.split(_LISTED_AFTER, 1)[1].strip().split("\n\n", 1)[0]
    return {n.strip() for n in listed.split(",") if n.strip()}


@pytest.fixture
def workspace(tmp_path: Path):
    from jenny.config import paths as paths_mod
    from jenny.runtime.context import get_runtime_context
    from jenny.utils.helpers import sync_workspace_templates

    previous = get_runtime_context().workspace_dir
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    paths_mod.set_workspace_dir(str(root))
    sync_workspace_templates(root, silent=True)
    try:
        yield root
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _loop_with_capture(workspace: Path) -> tuple[AgentLoop, dict]:
    """Un loop il cui provider registra la richiesta invece di mandarla."""
    captured: dict[str, Any] = {}

    async def chat_with_retry(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(content="done", tool_calls=[])

    async def chat_stream_with_retry(**kwargs: Any) -> LLMResponse:
        captured.update(kwargs)
        return LLMResponse(content="done", tool_calls=[])

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 1024
    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_stream_with_retry

    loop = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=workspace, model="test-model",
    )
    return loop, captured


def _offered_names(captured: dict) -> set[str]:
    return {d.get("function", d).get("name") for d in (captured.get("tools") or [])}


def _system_prompt(captured: dict) -> str:
    return captured["messages"][0]["content"]


# -- la derivazione ---------------------------------------------------------


async def test_the_inventory_is_derived_from_the_turn_registry(workspace):
    """Il test che copre Atlas senza nominarlo.

    Il registry qui non e ne quello del loop ne quello di Dream ne quello di
    Atlas: e inventato. Se l'inventario stampa esattamente questi nomi, allora
    nasce dal registry del turno e non da una fonte parallela — e la proprieta
    vale per qualunque registry, presente o futuro.
    """
    loop, captured = _loop_with_capture(workspace)
    invented = _registry("zzz_invented_alpha", "zzz_invented_beta")

    await loop.process_direct("ciao", session_key="test:derivation", tools=invented)

    assert _inventory_names(_system_prompt(captured)) == {
        "zzz_invented_alpha", "zzz_invented_beta",
    }


async def test_what_the_prompt_claims_is_what_the_model_is_offered(workspace):
    """L'invariante vera, letta dalla *stessa* richiesta.

    Non "compatibili": uguali. Le due liste rispondono alla stessa domanda, e
    finche a rispondere sono due pezzi di codice diversi possono divergere.
    """
    loop, captured = _loop_with_capture(workspace)
    restricted = _registry("zzz_only_this")

    await loop.process_direct("ciao", session_key="test:invariant", tools=restricted)

    assert _inventory_names(_system_prompt(captured)) == _offered_names(captured)


async def test_the_invariant_holds_for_the_default_registry_too(workspace):
    """Senza registry sostituito la fonte cambia, la coincidenza no."""
    loop, captured = _loop_with_capture(workspace)

    await loop.process_direct("ciao", session_key="test:default")

    offered = _offered_names(captured)
    assert offered, "il registry di default non ha prodotto nessun tool"
    assert _inventory_names(_system_prompt(captured)) == offered


# -- i casi reali, come documentazione dell'intento -------------------------


async def test_a_dream_turn_is_described_as_dream(workspace):
    """Il caso osservato sul telefono: Dream chiamo ``grep``, che non ha."""
    from jenny.agent.memory import MemoryStore

    loop, captured = _loop_with_capture(workspace)
    dream_tools = MemoryStore(workspace).build_dream_tools()

    await loop.process_direct("consolida", session_key="dream:test", tools=dream_tools)

    listed = _inventory_names(_system_prompt(captured))
    assert "apply_patch" in listed, "senza questo Dream non puo scrivere la memoria"
    assert listed.isdisjoint({"spawn", "cron", "grep", "subagent_status"})


async def test_an_atlas_turn_is_described_as_atlas(workspace):
    """Atlas e ibrido: legge ovunque, scrive un file solo — e ``grep`` ce l'ha."""
    from jenny.agent.atlas import AtlasStore

    loop, captured = _loop_with_capture(workspace)
    atlas_tools = AtlasStore(workspace).build_tools()

    await loop.process_direct("compila", session_key="atlas:test", tools=atlas_tools)

    listed = _inventory_names(_system_prompt(captured))
    assert {"grep", "find_files", "apply_patch"} <= listed
    assert listed.isdisjoint({"spawn", "cron", "subagent_status"})


# -- il gemello: la modalita, non solo l'elenco -----------------------------


_ORCHESTRATOR_BLOCK = "# Orchestrator Mode"


async def test_a_substituted_registry_is_not_the_orchestrator(workspace):
    """Il difetto gemello di quello sopra, trovato dall'audit delle giunture.

    ``orchestrator`` era un flag del costruttore, quindi ogni turno riceveva il
    blocco "non puoi scrivere file, delega con ``spawn``" — anche Dream e Atlas,
    che di mestiere scrivono file e ``spawn`` non ce l'hanno. Il prompt diceva a
    due agenti di non fare l'unica cosa per cui esistono.
    """
    loop, captured = _loop_with_capture(workspace)
    assert loop.orchestrator_mode is True

    await loop.process_direct("x", session_key="t:sub", tools=_registry("zzz_only_this"))
    substituted = _system_prompt(captured)

    await loop.process_direct("x", session_key="t:default")
    default = _system_prompt(captured)

    assert _ORCHESTRATOR_BLOCK in default
    assert _ORCHESTRATOR_BLOCK not in substituted
    assert "goes to a subagent via `spawn`" not in substituted


async def test_atlas_is_never_told_it_cannot_write(workspace):
    """Il caso concreto: scrivere e l'unico mestiere di Atlas."""
    from jenny.agent.atlas import AtlasStore

    loop, captured = _loop_with_capture(workspace)

    await loop.process_direct(
        "compila", session_key="atlas:t", tools=AtlasStore(workspace).build_tools(),
    )

    prompt = _system_prompt(captured)
    assert "you cannot execute code, write or patch" not in prompt
    assert _ORCHESTRATOR_BLOCK not in prompt
