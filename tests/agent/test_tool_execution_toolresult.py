"""Regression: il path diretto del runner deve srotolare ToolResult.

``_run_tool`` chiama ``tool.execute()`` direttamente (bypassa
``registry.execute()``, che è dove ToolResult verrebbe reso): senza srotolamento
il dataclass grezzo finiva nella history e ``json.dumps`` falliva con
"Object of type ToolResult is not JSON serializable" (visto su device).
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.runner import AgentRunner, AgentRunSpec
from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.result import ToolResult
from jenny.agent.tools.schema import tool_parameters_schema
from jenny.providers.base import ToolCallRequest


@tool_parameters(tool_parameters_schema())
class _StructuredTool(Tool):
    def __init__(self, result: ToolResult):
        self._result = result

    @property
    def name(self) -> str:
        return "structured"

    @property
    def description(self) -> str:
        return "returns a ToolResult"

    async def execute(self, **kwargs: Any) -> ToolResult:
        return self._result


def _spec(result: ToolResult) -> AgentRunSpec:
    registry = ToolRegistry()
    registry.register(_StructuredTool(result))
    return AgentRunSpec(
        initial_messages=[],
        tools=registry,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=8000,
    )


async def _run(result: ToolResult):
    runner = AgentRunner(provider=None)
    return await runner._run_tool(
        _spec(result),
        ToolCallRequest(id="call_1", name="structured", arguments={}),
        external_lookup_counts={},
        workspace_violation_counts={},
    )


async def test_success_toolresult_is_rendered():
    payload, event, exc = await _run(ToolResult.success("view: wiki\n<div>hi</div>"))
    assert payload == "view: wiki\n<div>hi</div>"
    json.dumps(payload)  # il payload deve essere serializzabile per la history
    assert event["status"] == "ok"
    assert exc is None


async def test_failure_toolresult_becomes_error_payload():
    payload, event, exc = await _run(
        ToolResult.failure("no WebUI client connected", code="ui_unavailable")
    )
    assert isinstance(payload, str)
    assert payload.startswith("Error: no WebUI client connected")
    json.dumps(payload)
    assert event["status"] == "error"
    assert exc is None
