"""Fase 3.4 — Le config dei tool hanno un'unica fonte (config.tool_schemas)
con re-export retro-compatibili, senza più la dance model_rebuild/lazy."""

from __future__ import annotations


def test_tool_configs_are_single_sourced_and_reexported() -> None:
    from jenny.agent.tools.filesystem import FileToolsConfig as FsFromTool
    from jenny.agent.tools.python_exec import PythonExecConfig as PyFromTool
    from jenny.config.schema import PythonExecConfig as PyFromSchema
    from jenny.config.tool_schemas import FileToolsConfig, PythonExecConfig

    # Stessa identità di classe da tutte le superfici d'import (re-export, non copie).
    assert PyFromTool is PythonExecConfig
    assert PyFromSchema is PythonExecConfig
    assert FsFromTool is FileToolsConfig


def test_config_resolves_tool_subconfigs_without_rebuild() -> None:
    from jenny.config.schema import Config
    from jenny.config.tool_schemas import PythonExecConfig

    cfg = Config()
    assert isinstance(cfg.tools.python_exec, PythonExecConfig)
    assert cfg.tools.python_exec.enable is True
    # default_factory concreto (non ForwardRef): dump completo senza errori.
    assert "allowed_modules" in cfg.tools.python_exec.model_dump()
