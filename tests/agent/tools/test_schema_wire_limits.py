"""Nessuno schema di tool deve sfondare il tetto di ripetizioni delle grammatiche.

I server che vincolano il tool-calling con una grammatica (llama.cpp) espandono
`maxLength`/`maxItems` in regole di ripetizione letterali e rifiutano oltre
`WIRE_STRING_LIMIT`. La grammatica è compilata dall'unione di TUTTI i tool, così
un solo campo fuori scala fa fallire con HTTP 400 ogni richiesta che porti tool
— non solo quelle che userebbero il tool colpevole. Vedi issue #2.
"""

from __future__ import annotations

from typing import Any, Iterator
from unittest.mock import MagicMock

from jenny.agent.tools.context import RequestContext
from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.long_task import CompleteGoalTool, LongTaskTool
from jenny.agent.tools.registry import ToolRegistry
from jenny.agent.tools.schema import WIRE_STRING_LIMIT

# Keyword che il convertitore JSON-Schema→grammatica traduce in ripetizioni.
# I bound interi (`minimum`/`maximum`) NON rientrano: diventano regole per-cifra,
# il cui costo cresce col numero di cifre, non col valore.
_REPETITION_KEYWORDS = ("minLength", "maxLength", "minItems", "maxItems")


def _iter_bounds(node: Any, path: str) -> Iterator[tuple[str, str, int]]:
    """Percorre lo schema e restituisce ogni bound di ripetizione trovato."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _REPETITION_KEYWORDS and isinstance(value, int):
                yield path, key, value
            yield from _iter_bounds(value, f"{path}.{key}")
    elif isinstance(node, list):
        for item in node:
            yield from _iter_bounds(item, path)


def _registered_tools() -> list[Any]:
    """I tool come li vede il gateway. Il ctx finto tiene `enabled()` permissivo:
    qui interessa il perimetro completo degli schemi, non quali tool siano
    attivi in una configurazione particolare."""
    registry = ToolRegistry()
    loader = ToolLoader()
    names = loader.load(MagicMock(), registry, scope="core")
    return [registry.get(name) for name in names]


def test_registered_tool_schemas_stay_under_the_grammar_cap():
    tools = _registered_tools()
    # Guardia anti-verde-fasullo: se il loader smette di restituire tool, il
    # test passerebbe senza aver controllato niente.
    assert len(tools) >= 15, f"expected the full core tool set, got {len(tools)}"

    violations = [
        f"{tool.name}{path}.{keyword} = {value} (cap {WIRE_STRING_LIMIT})"
        for tool in tools
        for path, keyword, value in _iter_bounds(tool.parameters, "")
        if value > WIRE_STRING_LIMIT
    ]
    assert not violations, (
        "These schemas break tool calling on grammar-constrained servers "
        "(llama.cpp answers HTTP 400 to every request, not just these tools):\n  "
        + "\n  ".join(violations)
    )


def test_goal_tools_declare_no_wire_length_bound():
    """Regressione puntuale della issue #2: `goal` era 12000, `recap` 8000.

    Verificato contro llama-server b10210: `long_task` da solo passa con `goal` a
    1000 e fallisce a 2000, quindi qui il vincolo non va abbassato, va assente —
    il tetto vive in ``execute()``.
    """
    goal = LongTaskTool(sessions=MagicMock()).parameters["properties"]["goal"]
    recap = CompleteGoalTool(sessions=MagicMock()).parameters["properties"]["recap"]

    assert "maxLength" not in goal
    assert "maxLength" not in recap


async def test_goal_length_is_enforced_in_execute():
    """Il tetto tolto dallo schema deve restare applicato a runtime."""
    tool = LongTaskTool(sessions=MagicMock())
    tool.set_context(
        RequestContext(channel="websocket", chat_id="c1", session_key="ws:c1", metadata={})
    )

    out = await tool.execute(goal="x" * 12_001)
    assert "at most 12000 characters" in out
