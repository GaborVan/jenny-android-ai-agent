"""``AgentHook.after_execute_tool``: una notifica per OGNI esito di tool call.

Perche il file esiste: prima di questo hook "questo tool ha finito" non era un
evento osservabile in nessun punto del codebase, e ``_run_tool`` ha sette uscite.
L'invariante che conta non e "l'hook funziona" ma **"nessun ramo lo dimentica"**:
un ramo muto e un tool fallito che sparisce dallo stream di attivita, cioe
esattamente il difetto per cui l'hook e stato aggiunto. Per questo il test e
tabellare sui rami e non un caso felice.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jenny.agent.hook import AgentHook, ToolResultHookContext
from jenny.agent.runner import AgentRunner, AgentRunSpec
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.schema import tool_parameters_schema
from jenny.providers.base import ToolCallRequest
from jenny.utils.runtime import external_lookup_signature


@tool_parameters(tool_parameters_schema())
class _ScriptedTool(Tool):
    """Tool che fa esattamente cio che il ramo sotto test richiede."""

    def __init__(self, name: str, behaviour: Any) -> None:
        self._name = name
        self._behaviour = behaviour

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "scripted"

    async def execute(self, **kwargs: Any) -> Any:
        if isinstance(self._behaviour, BaseException):
            raise self._behaviour
        if self._behaviour == "__sleep__":
            await asyncio.sleep(5)
            return "never"
        return self._behaviour


class _RecordingHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[ToolResultHookContext] = []

    async def after_execute_tool(self, context: ToolResultHookContext) -> None:
        self.calls.append(context)


class _ExplodingHook(AgentHook):
    async def after_execute_tool(self, context: ToolResultHookContext) -> None:
        raise RuntimeError("boom")


def _spec(hook: AgentHook | None, tool: Tool | None = None) -> AgentRunSpec:
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)
    return AgentRunSpec(
        initial_messages=[],
        tools=registry,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=8000,
        hook=hook,
    )


async def _run_tool(
    spec: AgentRunSpec,
    call: ToolCallRequest,
    *,
    external_lookup_counts: dict[str, int] | None = None,
) -> Any:
    runner = AgentRunner(provider=None)
    return await runner._run_tool(
        spec,
        call,
        external_lookup_counts if external_lookup_counts is not None else {},
        {},
    )


# --------------------------------------------------------------------------
# ogni ramo di uscita emette
# --------------------------------------------------------------------------


class TestEveryBranchEmits:
    async def test_success(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("ok_tool", "all good"))
        await _run_tool(spec, ToolCallRequest(id="c1", name="ok_tool", arguments={}))
        assert len(hook.calls) == 1
        ctx = hook.calls[0]
        assert ctx.name == "ok_tool"
        assert ctx.call_id == "c1"
        assert ctx.result == "all good"
        assert ctx.error is None

    async def test_prepare_error(self):
        hook = _RecordingHook()
        # Nessun tool registrato: ``prepare_call`` fallisce la risoluzione.
        spec = _spec(hook)
        await _run_tool(spec, ToolCallRequest(id="c2", name="ghost", arguments={}))
        assert len(hook.calls) == 1
        assert hook.calls[0].name == "ghost"
        assert str(hook.calls[0].result).startswith("Error: Tool 'ghost' not found")

    async def test_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "jenny.agent.tool_execution.tool_timeout_s", lambda: 0.01
        )
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("slow", "__sleep__"))
        await _run_tool(spec, ToolCallRequest(id="c3", name="slow", arguments={}))
        assert len(hook.calls) == 1
        assert "timed out" in str(hook.calls[0].result)

    async def test_exception(self):
        hook = _RecordingHook()
        boom = ValueError("nope")
        spec = _spec(hook, _ScriptedTool("raiser", boom))
        await _run_tool(spec, ToolCallRequest(id="c4", name="raiser", arguments={}))
        assert len(hook.calls) == 1
        # L'eccezione viaggia come OGGETTO: il formatter dell'attivita ne ricava
        # il nome di classe, che dalla stringa "Error: ..." non si distingue.
        assert hook.calls[0].error is boom
        assert hook.calls[0].result is None

    async def test_error_prefixed_string(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("softfail", "Error: file not found"))
        await _run_tool(spec, ToolCallRequest(id="c5", name="softfail", arguments={}))
        assert len(hook.calls) == 1
        assert hook.calls[0].result == "Error: file not found"

    async def test_boundary_violation(self):
        hook = _RecordingHook()
        spec = _spec(
            hook,
            _ScriptedTool("escaper", "Error: path is outside the configured workspace"),
        )
        payload, event, _ = await _run_tool(
            spec, ToolCallRequest(id="c6", name="escaper", arguments={"path": "/etc"})
        )
        # Il ramo di violazione ha una *sua* uscita, prima di quella ordinaria:
        # se emettesse solo l'altra questo test sarebbe l'unico ad accorgersene.
        assert event["status"] == "error"
        assert len(hook.calls) == 1
        assert "outside the configured workspace" in str(hook.calls[0].result)

    async def test_repeated_external_lookup_blocked(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("web_search", "results"))
        arguments = {"query": "same thing"}
        signature = external_lookup_signature("web_search", arguments)
        assert signature is not None
        counts = {signature: 99}
        await _run_tool(
            spec,
            ToolCallRequest(id="c7", name="web_search", arguments=arguments),
            external_lookup_counts=counts,
        )
        assert len(hook.calls) == 1
        assert "repeated external lookup blocked" in str(hook.calls[0].result)

    async def test_cancellation_is_the_only_silent_exit(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("cancelled", asyncio.CancelledError()))
        with pytest.raises(asyncio.CancelledError):
            await _run_tool(
                spec, ToolCallRequest(id="c8", name="cancelled", arguments={})
            )
        # Una cancellazione non e un esito del tool: la chiamata resta aperta e il
        # digest la marca "incomplete". Un evento qui mentirebbe.
        assert hook.calls == []


# --------------------------------------------------------------------------
# contratto del context
# --------------------------------------------------------------------------


class TestContextShape:
    async def test_duration_is_measured_and_non_negative(self, monkeypatch):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("ok_tool", "x"))
        await _run_tool(spec, ToolCallRequest(id="c1", name="ok_tool", arguments={}))
        assert hook.calls[0].duration_ms >= 0

    async def test_arguments_are_the_prepared_ones(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("ok_tool", "x"))
        await _run_tool(
            spec, ToolCallRequest(id="c1", name="ok_tool", arguments={"a": 1})
        )
        assert hook.calls[0].arguments == {"a": 1}

    async def test_missing_call_id_degrades_to_empty_string(self):
        hook = _RecordingHook()
        spec = _spec(hook, _ScriptedTool("ok_tool", "x"))
        await _run_tool(spec, ToolCallRequest(id=None, name="ok_tool", arguments={}))
        assert hook.calls[0].call_id == ""


# --------------------------------------------------------------------------
# isolamento e compatibilita
# --------------------------------------------------------------------------


class TestIsolation:
    async def test_a_raising_hook_cannot_fail_the_tool_call(self):
        spec = _spec(_ExplodingHook(), _ScriptedTool("ok_tool", "all good"))
        payload, event, exc = await _run_tool(
            spec, ToolCallRequest(id="c1", name="ok_tool", arguments={})
        )
        # Il tool e gia stato eseguito: un osservatore rotto non ha il diritto di
        # trasformarne il risultato in un errore.
        assert payload == "all good"
        assert event["status"] == "ok"
        assert exc is None

    async def test_a_hook_without_the_method_is_fine(self):
        class _Ancient:
            """Hook di terze parti che non conosce l'hook nuovo."""

        spec = _spec(None, _ScriptedTool("ok_tool", "all good"))
        spec.hook = _Ancient()  # type: ignore[assignment]
        payload, event, _ = await _run_tool(
            spec, ToolCallRequest(id="c1", name="ok_tool", arguments={})
        )
        assert payload == "all good"

    async def test_no_hook_at_all_is_fine(self):
        spec = _spec(None, _ScriptedTool("ok_tool", "all good"))
        payload, event, _ = await _run_tool(
            spec, ToolCallRequest(id="c1", name="ok_tool", arguments={})
        )
        assert payload == "all good"

    async def test_default_hook_is_a_no_op(self):
        """L'hook e condiviso con l'agente principale: il default non deve fare nulla."""
        base = AgentHook()
        assert await base.after_execute_tool(
            ToolResultHookContext(name="x", call_id="1", arguments={})
        ) is None
