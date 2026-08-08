"""Un host aggiunto ad app accesa deve arrivare al subagent, senza riavvio.

Il difetto che questi test bloccano e stato osservato sul telefono: host SSH
salvato dalle impostazioni alle 13:18, subagent ``sysadmin`` lanciato alle
13:32, e i tool SSH non c'erano. La config su disco era giusta; a essere vecchia
era la copia che l'agente aveva preso all'avvio.

L'asimmetria e il vero difetto: il *corpo* dei tool rilegge la config a ogni
chiamata (``resolve_target`` carica da disco), mentre il controllo "questo tool
esiste?" leggeva la copia dell'avvio. Togliere un host aveva effetto immediato,
aggiungerne uno no.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from jenny.agent.agent_types import AGENT_TYPES
from jenny.agent.loop import AgentLoop
from jenny.agent.subagent import SubagentManager
from jenny.bus.queue import MessageBus
from jenny.config.schema import Config, ToolsConfig
from jenny.config.tool_schemas import SshConfig, SshHostConfig
from jenny.providers.base import LLMProvider

SSH_TOOLS = {"ssh_hosts", "ssh_exec", "ssh_job", "ssh_transfer"}


def _provider() -> LLMProvider:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test"
    return provider


def _host() -> SshHostConfig:
    return SshHostConfig(alias="box", host="example.com", username="u")


def _manager(workspace: Path, **kw) -> SubagentManager:
    return SubagentManager(
        provider=_provider(),
        workspace=workspace,
        bus=MessageBus(),
        model="test",
        max_tool_result_chars=16_000,
        **kw,
    )


def _sysadmin_tools(sm: SubagentManager) -> set[str]:
    return set(sm._build_tools(agent_type=AGENT_TYPES["sysadmin"]).tool_names)


# -- il difetto vero --------------------------------------------------------


def test_a_host_added_after_construction_reaches_the_next_subagent(tmp_path):
    """Il caso del telefono, ridotto all'osso: aggiungere un host basta."""
    live = ToolsConfig(ssh=SshConfig(enable=True, hosts=[]))
    sm = _manager(
        tmp_path,
        tools_config=ToolsConfig(ssh=SshConfig(enable=True, hosts=[])),
        tools_config_provider=lambda: live,
    )

    assert SSH_TOOLS.isdisjoint(_sysadmin_tools(sm))

    live.ssh = SshConfig(enable=True, hosts=[_host()])

    assert SSH_TOOLS <= _sysadmin_tools(sm)


def test_a_host_removed_after_construction_disappears_too(tmp_path):
    """L'altra direzione: la freschezza non deve valere solo per le aggiunte."""
    live = ToolsConfig(ssh=SshConfig(enable=True, hosts=[_host()]))
    sm = _manager(tmp_path, tools_config=live, tools_config_provider=lambda: live)

    assert SSH_TOOLS <= _sysadmin_tools(sm)

    live.ssh = SshConfig(enable=True, hosts=[])

    assert SSH_TOOLS.isdisjoint(_sysadmin_tools(sm))


# -- la trappola: restrict_to_workspace non viene dal disco ------------------


def test_restrict_to_workspace_keeps_the_runtime_value_not_the_file_one(tmp_path):
    """``restrict_to_workspace`` sull'oggetto vivo e *risolto*, non letto.

    ``AgentLoop`` lo sovrascrive all'avvio con il valore effettivo della
    sandbox. Ripescarlo dal file insieme al resto cancellerebbe quella
    risoluzione — e con essa il confine dello workspace.
    """
    live = ToolsConfig(restrict_to_workspace=False)
    sm = _manager(
        tmp_path,
        tools_config=ToolsConfig(restrict_to_workspace=True),
        tools_config_provider=lambda: live,
    )

    assert sm._subagent_tools_config().restrict_to_workspace is True


def test_a_broken_provider_falls_back_instead_of_killing_the_spawn(tmp_path):
    """Config illeggibile: si degrada alla copia nota, non si perde il subagent."""

    def boom() -> ToolsConfig:
        raise OSError("config.json is unreadable")

    sm = _manager(
        tmp_path,
        tools_config=ToolsConfig(ssh=SshConfig(enable=True, hosts=[_host()])),
        tools_config_provider=boom,
    )

    assert SSH_TOOLS <= _sysadmin_tools(sm)


def test_without_a_provider_the_snapshot_is_still_used(tmp_path):
    """Chiamanti che non iniettano nulla (test, percorsi interni) non cambiano."""
    sm = _manager(tmp_path, tools_config=ToolsConfig(ssh=SshConfig(enable=True, hosts=[_host()])))

    assert SSH_TOOLS <= _sysadmin_tools(sm)


# -- il cablaggio vero, da AgentLoop fino al file su disco -------------------


def test_agent_loop_wires_a_provider_that_reads_config_json(tmp_path):
    """Il pezzo che nessun fake copre: il provider iniettato legge il file.

    Senza questo test la freschezza resterebbe una proprieta dei mock: e
    ``AgentLoop`` a doverla concedere al manager, e lo fa una volta sola in
    costruzione, quindi e proprio li che si rompe in silenzio.
    """
    from jenny.config import paths as paths_mod

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_path = workspace / "config.json"
    previous = paths_mod.get_workspace_path() if _workspace_is_set() else None
    paths_mod.set_workspace_dir(str(workspace))
    try:
        config = Config()
        config.tools.ssh = SshConfig(enable=True, hosts=[])
        config_path.write_text(
            json.dumps(config.model_dump(mode="json", by_alias=True)), encoding="utf-8"
        )

        # MagicMock senza ``spec``: ``AgentLoop`` legge anche ``provider.generation``,
        # che l'interfaccia astratta non dichiara.
        loop_provider = MagicMock()
        loop_provider.get_default_model.return_value = "test"
        loop_provider.generation.max_tokens = 1024
        loop = AgentLoop(
            bus=MessageBus(),
            provider=loop_provider,
            workspace=workspace,
            model="test",
        )
        assert SSH_TOOLS.isdisjoint(_sysadmin_tools(loop.subagents))

        config.tools.ssh = SshConfig(enable=True, hosts=[_host()])
        config_path.write_text(
            json.dumps(config.model_dump(mode="json", by_alias=True)), encoding="utf-8"
        )

        assert SSH_TOOLS <= _sysadmin_tools(loop.subagents)
    finally:
        paths_mod.set_workspace_dir(str(previous) if previous else "")


def _workspace_is_set() -> bool:
    from jenny.runtime.context import get_runtime_context

    return bool(get_runtime_context().workspace_dir)
