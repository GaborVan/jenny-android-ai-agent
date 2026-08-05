"""Tests per il registry dei tipi di agente (prompt + allowlist + default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.agent_types import (
    AGENT_TYPE_NAMES,
    AGENT_TYPES,
    DEFAULT_AGENT_TYPE,
    UnknownAgentTypeError,
    coerce_agent_type,
    get_agent_type,
    validate_agent_type,
)
from jenny.agent.subagent_records import SubagentRecord, SubagentSpec
from jenny.agent.tools.context import ToolContext
from jenny.agent.tools.file_state import FileStates
from jenny.agent.tools.loader import ToolLoader, ToolLoadError
from jenny.agent.tools.registry import ToolRegistry
from jenny.config.schema import ToolsConfig

# Insieme atteso per tipo: e la definizione del contratto, quindi va scritto per
# esteso invece di essere derivato dal codice sotto test.
EXPECTED_TOOLS = {
    "researcher": {"web_search", "web_fetch", "read_file", "list_dir", "write_file"},
    "writer": {"read_file", "list_dir", "write_file", "apply_patch"},
    "coder": {
        "read_file", "write_file", "edit_file", "list_dir", "apply_patch",
        "python_exec", "list_exec_sessions", "write_stdin", "get_recent_logs",
        "find_files", "grep",
    },
    "analyst": {"python_exec", "read_file", "list_dir", "write_file"},
    "operator": None,  # tutto lo scope subagent
}

_CODE_EXECUTION = {"python_exec", "write_stdin", "list_exec_sessions"}
_NETWORK = {"web_search", "web_fetch", "download_file"}


def _load(allow, tmp_path: Path) -> set[str]:
    """Carica lo scope subagent con l'allowlist data e ritorna i nomi."""
    registry = ToolRegistry()
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_state_store=FileStates(),
        # Contesto Android finto: senza, i tool web sono disabilitati
        # dall'ambiente e il set del researcher non sarebbe verificabile.
        android_context=object(),
    )
    return set(ToolLoader().load(ctx, registry, scope="subagent", allow=allow))


def test_five_types_exist() -> None:
    assert AGENT_TYPE_NAMES == ("researcher", "writer", "coder", "analyst", "operator")
    assert DEFAULT_AGENT_TYPE == "operator"


@pytest.mark.parametrize("name", ["researcher", "writer", "coder", "analyst", "operator"])
def test_declared_allowlist_matches_contract(name: str) -> None:
    expected = EXPECTED_TOOLS[name]
    declared = AGENT_TYPES[name].tools
    if expected is None:
        assert declared is None, "operator deve vedere tutto lo scope subagent"
    else:
        assert declared == frozenset(expected)


@pytest.mark.parametrize("name", ["researcher", "writer", "coder", "analyst"])
def test_type_registry_contains_exactly_its_tools(name: str, tmp_path: Path) -> None:
    loaded = _load(AGENT_TYPES[name].tools, tmp_path)
    assert loaded == EXPECTED_TOOLS[name]


def test_operator_gets_the_whole_subagent_scope(tmp_path: Path) -> None:
    assert _load(None, tmp_path) == _load(AGENT_TYPES["operator"].tools, tmp_path)


def test_allow_none_is_todays_behaviour(tmp_path: Path) -> None:
    registry = ToolRegistry()
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_state_store=FileStates(),
        android_context=object(),
    )
    without_kwarg = set(ToolLoader().load(ctx, registry, scope="subagent"))
    assert without_kwarg == _load(None, tmp_path)


def test_researcher_cannot_execute_code(tmp_path: Path) -> None:
    """Confine di sicurezza: chi legge il web non esegue codice."""
    loaded = _load(AGENT_TYPES["researcher"].tools, tmp_path)
    assert not (loaded & _CODE_EXECUTION)
    assert "apply_patch" not in loaded
    assert "edit_file" not in loaded


@pytest.mark.parametrize("name", ["writer", "analyst", "coder"])
def test_non_researcher_types_have_no_network(name: str, tmp_path: Path) -> None:
    """Confine simmetrico: chi esegue/scrive non va a prendersi la fonte."""
    assert not (_load(AGENT_TYPES[name].tools, tmp_path) & _NETWORK)


def test_unknown_allow_entry_raises_at_load(tmp_path: Path) -> None:
    with pytest.raises(ToolLoadError) as exc:
        _load({"read_file", "reed_file"}, tmp_path)
    assert "reed_file" in str(exc.value)
    # L'errore deve elencare i tool noti, altrimenti il typo va indovinato.
    assert "read_file" in str(exc.value)


def test_config_disabled_tool_is_not_a_typo(tmp_path: Path) -> None:
    """Un tool spento dall'ambiente non deve far abortire il caricamento.

    ``enabled()`` dice no per ragioni di config/dispositivo: se questo fosse
    fatale, spegnere i tool web in config renderebbe il researcher inavviabile.
    """
    registry = ToolRegistry()
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        file_state_store=FileStates(),
        android_context=None,  # tool web disabilitati
    )
    loaded = set(ToolLoader().load(
        ctx, registry, scope="subagent", allow=AGENT_TYPES["researcher"].tools
    ))
    assert "web_search" not in loaded
    assert "read_file" in loaded


# -- risoluzione / validazione -------------------------------------------------


def test_get_agent_type_defaults_to_operator() -> None:
    assert get_agent_type(None).name == "operator"


def test_unknown_type_error_names_valid_types() -> None:
    with pytest.raises(UnknownAgentTypeError) as exc:
        validate_agent_type("researchers")
    message = str(exc.value)
    for name in AGENT_TYPE_NAMES:
        assert name in message


def test_spec_rejects_unknown_agent_type() -> None:
    with pytest.raises(UnknownAgentTypeError):
        SubagentSpec(task="t", label="l", agent_type="mad_scientist")


@pytest.mark.asyncio
async def test_spawn_rejects_unknown_agent_type(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000,
    )
    with pytest.raises(UnknownAgentTypeError):
        await mgr.spawn(task="t", agent_type="nope")
    assert not mgr._running_tasks


def test_record_replay_degrades_unknown_type_to_operator() -> None:
    """Un record su disco con un tipo scomparso resta rilanciabile."""
    raw = {
        "task_id": "abc12345",
        "lineage_id": "lin12345",
        "attempt": 1,
        "spec": {"task": "do it", "label": "l", "agent_type": "phrenologist"},
        "state": "failed",
    }
    record = SubagentRecord.from_dict(raw)
    assert record.spec.agent_type == DEFAULT_AGENT_TYPE


def test_coerce_agent_type_passes_known_names() -> None:
    assert coerce_agent_type("coder") == "coder"
    assert coerce_agent_type(None) == DEFAULT_AGENT_TYPE
    assert coerce_agent_type(17) == DEFAULT_AGENT_TYPE


# -- default per tipo ----------------------------------------------------------


def test_type_max_iterations_can_only_narrow_the_configured_cap(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from jenny.agent.subagent import SubagentManager
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000, max_iterations=25,
    )
    # coder chiede 120, la config concede 25: vince la config.
    assert mgr._type_max_iterations(AGENT_TYPES["coder"]) == 25
    # operator non ha default proprio: eredita.
    assert mgr._type_max_iterations(AGENT_TYPES["operator"]) == 25


@pytest.mark.asyncio
async def test_run_subagent_uses_type_defaults_and_prompt(tmp_path: Path) -> None:
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    mgr._announce_result = AsyncMock()
    seen: dict[str, object] = {}

    async def fake_run(spec):
        seen["temperature"] = spec.temperature
        seen["max_iterations"] = spec.max_iterations
        seen["tools"] = set(spec.tools.tool_names)
        seen["system"] = spec.initial_messages[0]["content"]
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    spec = SubagentSpec(task="analyse", label="l", agent_type="analyst")
    status = SubagentStatus(
        task_id="s1", label="l", task_description="analyse", started_at=time.monotonic(),
    )
    await mgr._run_subagent("s1", spec, status)

    assert seen["temperature"] == AGENT_TYPES["analyst"].temperature
    assert seen["max_iterations"] == AGENT_TYPES["analyst"].max_iterations
    assert "web_search" not in seen["tools"]
    assert "Role: analyst" in str(seen["system"])


@pytest.mark.asyncio
async def test_explicit_temperature_beats_type_default(tmp_path: Path) -> None:
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from jenny.agent.subagent import SubagentManager, SubagentStatus
    from jenny.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider, workspace=tmp_path, bus=MessageBus(),
        max_tool_result_chars=16_000,
    )
    mgr._announce_result = AsyncMock()
    seen: dict[str, object] = {}

    async def fake_run(spec):
        seen["temperature"] = spec.temperature
        return SimpleNamespace(
            stop_reason="done", final_content="ok", error=None, tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)
    spec = SubagentSpec(task="write", label="l", agent_type="writer", temperature=0.0)
    status = SubagentStatus(
        task_id="s1", label="l", task_description="write", started_at=time.monotonic(),
    )
    await mgr._run_subagent("s1", spec, status)

    assert seen["temperature"] == 0.0


def test_every_type_has_a_prompt_template() -> None:
    from jenny.utils.prompt_templates import render_template

    for name, atype in AGENT_TYPES.items():
        rendered = render_template(atype.prompt_template, strip=True)
        assert f"Role: {name}" in rendered
