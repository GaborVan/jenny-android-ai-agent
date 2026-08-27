"""Il prompt del subagent dice la sola lettura DELLA SPEC, non quella del chiamante.

Passo **T4.8** di ``roadmap/audit-taccuino-corrections.md``.

``_build_subagent_prompt`` viene chiamato **fuori** dal blocco
``enter_workspace_scope(spec.workspace_scope)``: se il flag ``readonly`` lo
leggesse dall'ambiente, direbbe il modo del *turno del chiamante* mentre il run
lega quello della *spec*. Sullo spawn i due coincidono per caso — ``create_task``
copia il contesto del turno che ha spawnato — ma su ``restart`` e ``send`` no:
rilanciare una spec scrivibile da dentro un turno in sola lettura dava a un
subagent che poteva scrivere un prompt che diceva "non puoi", e il verso opposto
produceva esattamente il run buttato che il commento accanto dice di aver
risolto (il difetto visto sul telefono il 22/08).

Perche' questi test passano dai **veri** ``restart`` / ``send``, e non chiamano
``_build_subagent_prompt`` a mano: il flag nasceva da un ContextVar, e un
ContextVar si comporta in modo diverso a seconda di *chi* copia il contesto e
*quando*. Un test che costruisse il prompt sul thread di prova con argomenti
fatti a mano non vedrebbe mai la divergenza, perche' la divergenza *e'* la copia
del contesto fatta da ``asyncio.create_task`` dentro ``_launch``.

Le due asserzioni vanno sempre in coppia — quel che il prompt **dice** e quel
che il run **lega** — perche' il difetto non e' "il flag e' sbagliato" ma "i due
non concordano", e una sola delle due meta' non lo mostrerebbe.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from jenny.agent.runner import AgentRunResult, AgentRunSpec
from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider
from jenny.security.workspace_access import (
    WorkspaceScope,
    build_workspace_scope,
    current_workspace_scope,
    enter_workspace_scope,
)
from jenny.session.manager import SessionManager

_READONLY_BLOCK = "# Read-Only Turn"


class _Run:
    """Quel che un run ha visto: il prompt ricevuto e lo scope legato."""

    def __init__(self, spec: AgentRunSpec, scope: WorkspaceScope | None) -> None:
        self.system_prompt = str(spec.initial_messages[0].get("content", ""))
        self.bound_scope = scope

    @property
    def claims_readonly(self) -> bool:
        return _READONLY_BLOCK in self.system_prompt

    @property
    def bound_writable(self) -> bool:
        return self.bound_scope is None or self.bound_scope.writable


def _manager(tmp_path: Path) -> SubagentManager:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    return SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        model="test-model",
        max_tool_result_chars=16_000,
        # Watchdog spento: lo stallo ha i suoi test, qui non deve interferire.
        stall_threshold_s=0.0,
        session_manager=SessionManager(tmp_path),
    )


def _recording_runner(sm: SubagentManager) -> list[_Run]:
    """Runner che completa subito e registra prompt + scope legato.

    Lo scope si legge **dentro** ``run``: e' l'unico punto in cui si vede quel
    che il subagent avra' davvero, cioe' l'effetto del ``with`` in
    ``_run_subagent``.
    """
    runs: list[_Run] = []

    async def _run(spec: AgentRunSpec) -> AgentRunResult:
        runs.append(_Run(spec, current_workspace_scope()))
        return AgentRunResult(
            final_content="done",
            messages=list(spec.initial_messages) + [{"role": "assistant", "content": "done"}],
            stop_reason="completed",
        )

    sm.runner.run = _run  # type: ignore[method-assign]
    return runs


async def _settle(sm: SubagentManager) -> None:
    tasks = [t for t in sm._running_tasks.values() if not t.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)
    await sm._cancel_stall_watchdog()


async def _spawn_and_settle(sm: SubagentManager, scope: WorkspaceScope | None) -> str:
    await sm.spawn("do the job", session_key="unified:default", workspace_scope=scope)
    lineage = next(iter(sm._task_statuses.values())).lineage_id
    await _settle(sm)
    return lineage


def _writable(tmp_path: Path) -> WorkspaceScope:
    return build_workspace_scope(tmp_path, "restricted")


def _readonly(tmp_path: Path) -> WorkspaceScope:
    return build_workspace_scope(tmp_path, "restricted").without_write_access()


# ── restart: i due versi ─────────────────────────────────────────────────


async def test_restarting_a_writable_spec_from_a_readonly_turn_does_not_claim_readonly(
    tmp_path: Path,
) -> None:
    """(a) Il verso che sprecava il lavoro: prompt chiuso, scope aperto.

    Il subagent poteva scrivere e gli si diceva di non provarci: ogni scrittura
    del suo piano diventava una descrizione, e il run tornava a mani vuote.
    """
    sm = _manager(tmp_path)
    runs = _recording_runner(sm)
    lineage = await _spawn_and_settle(sm, _writable(tmp_path))
    assert not runs[0].claims_readonly and runs[0].bound_writable

    # Il turno del chiamante e' in sola lettura; la spec no. ``restart`` crea il
    # task **qui dentro**, quindi il contesto copiato porta lo scope del turno:
    # e' esattamente la condizione di produzione.
    with enter_workspace_scope(_readonly(tmp_path)):
        await sm.restart(lineage, manual=True, grace_s=0.05)
    await _settle(sm)

    assert len(runs) == 2
    assert runs[1].bound_writable, "il run lega lo scope della spec: scrivibile"
    assert not runs[1].claims_readonly, (
        "il prompt diceva 'non puoi scrivere' a un subagent che poteva: "
        "il flag veniva dal turno del chiamante, non dalla spec"
    )


async def test_restarting_a_readonly_spec_from_a_writable_turn_still_claims_readonly(
    tmp_path: Path,
) -> None:
    """(b) Il verso opposto: quel che il commento del 22/08 dice di aver risolto.

    Senza il blocco nel prompt il subagent pianifica e scrive, e il cancello
    rifiuta tutto: il confine tiene, il run e' buttato.
    """
    sm = _manager(tmp_path)
    runs = _recording_runner(sm)
    lineage = await _spawn_and_settle(sm, _readonly(tmp_path))
    assert runs[0].claims_readonly

    with enter_workspace_scope(_writable(tmp_path)):
        await sm.restart(lineage, manual=True, grace_s=0.05)
    await _settle(sm)

    assert len(runs) == 2
    assert not runs[1].bound_writable, "il run lega lo scope della spec: sola lettura"
    assert runs[1].claims_readonly, (
        "il subagent gira in sola lettura e non lo sa: pianifichera' scritture "
        "che il cancello rifiutera' una per una"
    )


# ── send: il resume di una storia potata dalla testa ─────────────────────


async def test_a_resume_without_a_stored_system_message_takes_the_spec_mode(
    tmp_path: Path,
) -> None:
    """``send`` -> ``_resume_lineage``, il caso in cui il prompt si rifa'.

    Una storia potata dalla testa (``enforce_file_cap``) ha perso il proprio
    system message, e ``_run_subagent`` gliene mette uno fresco: e' l'unico ramo
    del resume in cui il prompt viene ricostruito, quindi l'unico in cui la
    divergenza fra spec e ambiente si vede.
    """
    sm = _manager(tmp_path)
    runs = _recording_runner(sm)
    lineage = await _spawn_and_settle(sm, _writable(tmp_path))
    # Storia senza system message, come dopo una potatura dalla testa.
    assert sm._history.save(
        lineage,
        "unified:default",
        [
            {"role": "user", "content": "do the job"},
            {"role": "assistant", "content": "half done"},
        ],
    )

    with enter_workspace_scope(_readonly(tmp_path)):
        outcome = await sm.send(lineage, "keep going")
    await _settle(sm)

    assert outcome.mode == "resumed"
    # Il prompt e' stato davvero ricostruito, non ereditato dalla storia.
    assert "# Subagent" in runs[-1].system_prompt
    assert runs[-1].bound_writable
    assert not runs[-1].claims_readonly, (
        "il resume ha ricostruito il prompt dal turno del chiamante invece che "
        "dalla spec"
    )


# ── spawn: invariato, nei due modi ───────────────────────────────────────


async def test_the_spawn_path_is_unchanged(tmp_path: Path) -> None:
    """(c) Sullo spawn spec e ambiente concordano, e devono continuare a farlo."""
    sm = _manager(tmp_path)
    runs = _recording_runner(sm)

    with enter_workspace_scope(_readonly(tmp_path)):
        await sm.spawn(
            "do the job", session_key="unified:default", workspace_scope=_readonly(tmp_path)
        )
    await _settle(sm)
    assert runs[0].claims_readonly and not runs[0].bound_writable

    with enter_workspace_scope(_writable(tmp_path)):
        await sm.spawn(
            "do the job", session_key="unified:default", workspace_scope=_writable(tmp_path)
        )
    await _settle(sm)
    assert not runs[1].claims_readonly and runs[1].bound_writable


async def test_a_spec_without_a_scope_still_reads_the_ambient_one(tmp_path: Path) -> None:
    """Il fallback all'ambiente non e' un residuo: e' l'unica risposta giusta.

    Con ``workspace_scope=None`` il ``with enter_workspace_scope(None)`` di
    ``_run_subagent`` e' un no-op, quindi lo scope legato durante il run **e'**
    quello copiato dal turno che ha spawnato. Leggere solo la spec direbbe qui
    "puoi scrivere" a un subagent che non puo': lo stesso run buttato, dall'altra
    porta.
    """
    sm = _manager(tmp_path)
    runs = _recording_runner(sm)

    with enter_workspace_scope(_readonly(tmp_path)):
        await sm.spawn("do the job", session_key="unified:default")
    await _settle(sm)

    assert not runs[0].bound_writable, "lo scope del turno resta legato dentro il run"
    assert runs[0].claims_readonly
