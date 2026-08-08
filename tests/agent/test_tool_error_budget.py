"""Budget di errori tool: quando un agente a ``fail_on_tool_error`` si arrende.

Il difetto che questi test pinnano e stato osservato sul device: un subagent
researcher aveva completato due ``web_search`` e tre ``web_fetch``, poi ha letto
un output spillato con ``read_file(offset=40)`` su un file di meno di 40 righe.
``read_file`` ha risposto "Error: offset 40 is beyond end of file (N lines)" e
quel singolo errore ha chiuso il run con ``stop_reason="tool_error"``, buttando
via un lavoro finito. L'agente principale, nella stessa situazione, riceve il
retry hint e prosegue.

Il budget ha tre contatori e un solo knob (vedi ``ToolErrorBudget``): serie
consecutiva, totale del run, e violazioni di boundary di sicurezza contate a
parte.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jenny.agent.runner import AgentRunner, AgentRunResult, AgentRunSpec
from jenny.agent.subagent import (
    DEFAULT_TOOL_ERROR_BUDGET,
    SubagentManager,
    SubagentSpec,
    SubagentStatus,
)
from jenny.agent.tool_error_policy import TOOL_ERROR_RETRY_HINT
from jenny.agent.tool_execution import ToolErrorBudget
from jenny.bus.queue import MessageBus
from jenny.providers.base import LLMProvider, LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = 16_000

_OFFSET_ERROR = "Error: offset 40 is beyond end of file (12 lines)"
_SSRF_ERROR = "Error: internal/private URL detected for http://10.0.0.1/admin"
_WORKSPACE_ERROR = "Error: path /etc/passwd is outside the configured workspace"


async def _run(
    script: list[str | tuple[str, str]],
    *,
    tool_error_budget: int | None = DEFAULT_TOOL_ERROR_BUDGET,
    fail_on_tool_error: bool = True,
    max_iterations: int = 40,
) -> AgentRunResult:
    """Gira un run in cui il modello chiama un tool per voce di ``script``.

    Ogni voce e il risultato che il tool restituisce (``"ok..."`` o
    ``"Error: ..."``), eventualmente come ``(tool_name, result)``. Esaurito lo
    script il modello risponde e il run termina: cosi "il run e sopravvissuto"
    si osserva come ``final_content``, non come assenza di eccezioni.
    """
    steps = [step if isinstance(step, tuple) else (f"probe_{i}", step)
             for i, step in enumerate(script)]
    cursor = {"n": 0}

    async def chat_with_retry(*, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        index = cursor["n"]
        if index >= len(steps):
            return LLMResponse(content="done", tool_calls=[], usage={})
        return LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id=f"call_{index}", name=steps[index][0], arguments={})],
            usage={},
        )

    async def execute(name: str, params: Any, **kwargs: Any) -> str:
        result = steps[cursor["n"]][1]
        cursor["n"] += 1
        return result

    provider = MagicMock(spec=LLMProvider)
    provider.chat_with_retry = chat_with_retry
    provider.chat_stream_with_retry = chat_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(side_effect=execute)

    return await AgentRunner(provider).run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "go"}],
        tools=tools,
        model="test-model",
        max_iterations=max_iterations,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        fail_on_tool_error=fail_on_tool_error,
        tool_error_budget=tool_error_budget,
    ))


# ---------------------------------------------------------------------------
# Il budget assorbe gli errori onesti
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("errors", [1, 2, 3])
@pytest.mark.asyncio
async def test_recoverable_tool_errors_within_budget_do_not_kill_the_run(errors: int):
    result = await _run([_OFFSET_ERROR] * errors + ["file content"])

    assert result.stop_reason == "completed"
    assert result.final_content == "done"
    assert result.error is None
    assert sum(1 for e in result.tool_events if e["status"] == "error") == errors


@pytest.mark.asyncio
async def test_tool_error_within_budget_reaches_the_model_with_the_retry_hint():
    """Dentro il budget l'errore torna al modello come feedback, non come verdetto.

    E la stessa cosa che riceve l'agente principale: senza il retry hint l'errore
    sarebbe indistinguibile da un risultato qualunque.
    """
    result = await _run([_OFFSET_ERROR, "file content"])

    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert tool_messages[0]["content"] == _OFFSET_ERROR + TOOL_ERROR_RETRY_HINT


@pytest.mark.asyncio
async def test_fourth_consecutive_tool_error_exhausts_the_budget():
    result = await _run([_OFFSET_ERROR] * 4)

    assert result.stop_reason == "tool_error"
    assert result.error is not None
    assert "4 recoverable tool errors in a row (budget 3)" in result.error
    # Il testo dell'ultimo fallimento resta nel messaggio: e cio che dice
    # all'orchestratore *cosa* correggere nel rilancio.
    assert "offset 40 is beyond end of file" in result.error


# ---------------------------------------------------------------------------
# Consecutivi vs totali, nelle due direzioni
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_success_clears_the_consecutive_streak_but_not_the_total():
    """Sei errori a gruppi di tre, separati da successi: il run sopravvive.

    Direzione "consecutivi": nessun gruppo supera il budget di 3, quindi il
    rilevatore di flailing non scatta, e il totale (6) resta esattamente sul
    tetto ``3 * 2``. Un agente che sbaglia, si corregge e riparte non deve morire
    per la somma dei suoi errori.
    """
    result = await _run(
        [_OFFSET_ERROR] * 3 + ["file content"] + [_OFFSET_ERROR] * 3 + ["file content"]
    )

    assert result.stop_reason == "completed"
    assert sum(1 for e in result.tool_events if e["status"] == "error") == 6


@pytest.mark.asyncio
async def test_alternating_success_and_failure_still_exhausts_the_total_budget():
    """Direzione "totali": un buono e uno sbagliato per sempre deve finire.

    La serie consecutiva non supera mai 1, quindi un budget che si azzera a ogni
    successo non vedrebbe *mai* questo caso: l'agente progredisce sulla carta e
    brucia tutto il budget di iterazioni ripetendo lo stesso errore. Il settimo
    errore sfonda il tetto totale (3 * 2 = 6).
    """
    result = await _run([_OFFSET_ERROR, "file content"] * 7)

    assert result.stop_reason == "tool_error"
    assert result.error is not None
    assert "7 recoverable tool errors in this run (budget 6)" in result.error


@pytest.mark.asyncio
async def test_consecutive_rule_fires_before_the_total_ceiling_is_reached():
    """Quattro di fila chiudono il run col totale ancora sotto il tetto (4 < 6)."""
    result = await _run([_OFFSET_ERROR] * 4)

    assert result.error is not None
    assert "in a row" in result.error
    assert "in this run" not in result.error


# ---------------------------------------------------------------------------
# Boundary di sicurezza
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_boundary_violation_is_not_paid_from_the_ordinary_budget():
    """Un blocco SSRF non consuma i tentativi riservati agli errori onesti.

    Ragionamento: le due cose non si assomigliano. Un ``offset`` sbagliato e una
    mira imprecisa su un parametro che il modello deve indovinare, e ritentare e
    esattamente cio che serve; un blocco SSRF e un rifiuto non aggirabile che
    dice al modello di smettere. Contarli sullo stesso budget significherebbe
    che una singola violazione riduce la tolleranza agli errori di mira — cioe la
    sicurezza renderebbe l'agente piu fragile invece che solo piu limitato.
    """
    result = await _run([_SSRF_ERROR] + [_OFFSET_ERROR] * 3 + ["file content"])

    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_second_ssrf_block_ends_the_run_immediately():
    """Nessun "tre tentativi gratis" contro un URL privato.

    L'allowance di boundary e 1, non ``limit``: il payload del primo blocco dice
    esplicitamente di non ritentare per altre vie (``SSRF_BOUNDARY_NOTE``), quindi
    la prima violazione serve a far cambiare strada e la seconda dimostra che non
    e servita. Non c'e nessun recupero da comprare con altri tentativi.
    """
    result = await _run([_SSRF_ERROR, _SSRF_ERROR, "never reached"])

    assert result.stop_reason == "tool_error"
    assert result.error is not None
    assert "2 safety-boundary violations in this run (allowance 1)" in result.error


@pytest.mark.asyncio
async def test_first_ssrf_block_still_reaches_the_model_as_a_soft_error():
    result = await _run([_SSRF_ERROR, "https://example.org fetched"])

    assert result.stop_reason == "completed"
    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert "non-bypassable security boundary" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_repeated_workspace_violation_ends_the_run_on_the_same_allowance():
    """Anche il boundary di workspace e un boundary: stessa allowance dell'SSRF."""
    result = await _run([_WORKSPACE_ERROR, _WORKSPACE_ERROR, "never reached"])

    assert result.stop_reason == "tool_error"
    assert result.error is not None
    assert "safety-boundary violations" in result.error


@pytest.mark.asyncio
async def test_ssrf_block_and_ordinary_errors_are_counted_separately():
    """Una violazione piu quattro errori ordinari: muore per gli ordinari."""
    result = await _run([_SSRF_ERROR] + [_OFFSET_ERROR] * 4)

    assert result.error is not None
    assert "recoverable tool errors in a row" in result.error


# ---------------------------------------------------------------------------
# Compatibilita con i chiamanti esistenti
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_budget_the_first_tool_error_still_ends_the_run():
    """``fail_on_tool_error`` senza budget: comportamento storico, verbatim."""
    result = await _run([_OFFSET_ERROR, "never reached"], tool_error_budget=None)

    assert result.stop_reason == "tool_error"
    assert result.error == f"Error: RuntimeError: {_OFFSET_ERROR}"


@pytest.mark.asyncio
async def test_without_a_budget_a_boundary_violation_is_still_not_fatal():
    """La modalita storica non ha mai chiuso un run su una violazione, e non inizia qui."""
    result = await _run(
        [_SSRF_ERROR, _SSRF_ERROR, "recovered"], tool_error_budget=None,
    )

    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_a_zero_budget_is_the_legacy_strictness_expressed_as_a_number():
    result = await _run([_OFFSET_ERROR, "never reached"], tool_error_budget=0)

    assert result.stop_reason == "tool_error"
    assert result.error is not None
    assert "1 recoverable tool errors in a row (budget 0)" in result.error


@pytest.mark.asyncio
async def test_without_fail_on_tool_error_no_budget_can_be_exhausted():
    """L'agente principale non ha un tetto: ogni errore e solo feedback."""
    result = await _run(
        [_OFFSET_ERROR] * 10 + ["file content"],
        fail_on_tool_error=False,
        tool_error_budget=DEFAULT_TOOL_ERROR_BUDGET,
    )

    assert result.stop_reason == "completed"


# ---------------------------------------------------------------------------
# Contabilita del budget in isolamento
# ---------------------------------------------------------------------------


def test_budget_derives_both_ceilings_from_one_knob():
    budget = ToolErrorBudget(limit=3)

    assert budget.total_limit == 6
    assert budget.boundary_limit == 1


def test_legacy_budget_has_no_ceilings_to_derive():
    budget = ToolErrorBudget()

    assert budget.limit is None
    assert budget.total_limit == 0
    assert budget.boundary_limit == 0
    assert budget.spend_boundary() is None


def test_credit_clears_only_the_consecutive_counter():
    budget = ToolErrorBudget(limit=3)
    budget.spend_ordinary()
    budget.spend_ordinary()
    budget.credit()

    assert budget.consecutive == 0
    assert budget.total == 2


def test_from_spec_normalizes_a_negative_budget_to_zero():
    spec = AgentRunSpec(
        initial_messages=[],
        tools=MagicMock(),
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        tool_error_budget=-5,
    )

    assert ToolErrorBudget.from_spec(spec).limit == 0


# ---------------------------------------------------------------------------
# Il progresso parziale arriva all'orchestratore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausted_budget_still_reports_the_completed_steps():
    """Cio che ha salvato entrambi i fallimenti sul device: il rilancio informato.

    Con il budget esaurito l'orchestratore deve ricevere i passi completati *e* il
    fallimento, altrimenti puo solo rilanciare il lavoro identico e sperare.
    """
    result = await _run([
        ("web_search", "3 results for titan 2 price"),
        ("web_fetch", "page body"),
        ("web_fetch", "another page body"),
    ] + [("read_file", _OFFSET_ERROR)] * 4)

    assert result.stop_reason == "tool_error"

    partial = SubagentManager._format_partial_progress(result)
    assert "Completed steps:" in partial
    assert "web_search" in partial
    assert "web_fetch" in partial
    assert "Failure:" in partial
    assert "read_file" in partial
    assert "offset 40 is beyond end of file" in partial


# ---------------------------------------------------------------------------
# Wiring del manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_runs_with_the_configured_tool_error_budget(tmp_path):
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        tool_error_budget=5,
    )
    captured: list[AgentRunSpec] = []

    async def fake_run(spec: AgentRunSpec) -> AgentRunResult:
        captured.append(spec)
        return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

    manager.runner.run = fake_run
    status = SubagentStatus(
        task_id="t1", label="label", task_description="do task",
        started_at=time.monotonic(),
    )
    with patch.object(manager, "_announce_result", new_callable=AsyncMock):
        await manager._run_subagent("t1", SubagentSpec(task="do task", label="label"), status)

    assert captured[0].fail_on_tool_error is True
    assert captured[0].tool_error_budget == 5


def test_subagent_manager_defaults_to_the_agreed_budget(tmp_path):
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    manager = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    )

    assert manager.tool_error_budget == DEFAULT_TOOL_ERROR_BUDGET == 3
