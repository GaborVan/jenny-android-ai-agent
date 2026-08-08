"""I knob dei subagent arrivano dal config fino al ``SubagentManager``.

Un default nello schema che non e cablato non fa nulla, e nessuno degli altri
test lo nota: quelli sul comportamento costruiscono il manager a mano passando il
knob, quindi passano anche se ``from_config`` non lo legge. Qui si pinna la
catena ``Config -> AgentLoop.from_config -> SubagentManager``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jenny.agent.loop import AgentLoop
from jenny.config.schema import Config


def _loop(tmp_path, **defaults):
    config = Config.model_validate({
        "agents": {"defaults": defaults},
        "workspace": {"path": str(tmp_path)},
    })
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    # ``provider`` passato come extra: ``from_config`` lo preferisce alla factory,
    # cosi il test non dipende dai provider configurati.
    return AgentLoop.from_config(config, bus=MagicMock(), provider=provider)


@pytest.mark.parametrize(
    ("key", "value", "attr"),
    [
        ("maxConcurrentSubagents", 4, "max_concurrent_subagents"),
        ("subagentStallThresholdSeconds", 42, "stall_threshold_s"),
        ("subagentToolErrorBudget", 7, "tool_error_budget"),
    ],
)
def test_config_knobs_reach_the_manager(tmp_path, key, value, attr) -> None:
    loop = _loop(tmp_path, **{key: value})
    assert getattr(loop.subagents, attr) == value


def test_defaults_reach_the_manager(tmp_path) -> None:
    """Senza override valgono i default dello schema, non quelli del manager."""
    from jenny.config.schema import AgentDefaults

    loop = _loop(tmp_path)
    defaults = AgentDefaults()
    assert loop.subagents.max_concurrent_subagents == defaults.max_concurrent_subagents
    assert loop.subagents.tool_error_budget == defaults.subagent_tool_error_budget
    assert loop.subagents.stall_threshold_s == defaults.subagent_stall_threshold_seconds


def test_a_zero_budget_survives_the_wiring(tmp_path) -> None:
    """Zero e un valore, non "non impostato": ripristina il comportamento vecchio.

    Se la catena usasse ``or`` invece di ``is not None``, lo zero diventerebbe il
    default e l'unico modo di tornare al vecchio comportamento sparirebbe.
    """
    loop = _loop(tmp_path, subagentToolErrorBudget=0)
    assert loop.subagents.tool_error_budget == 0
