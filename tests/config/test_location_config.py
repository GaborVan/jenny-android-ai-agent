"""Test per LocationConfig (posizione dispositivo) e il suo aggancio a ToolsConfig."""

from __future__ import annotations

from jenny.config.schema import Config, ToolsConfig
from jenny.config.tool_schemas import LocationConfig


def test_defaults():
    cfg = LocationConfig()
    assert cfg.enable is True  # default ON
    assert cfg.telegram_ttl_s == 3600  # 1 h
    assert cfg.fresh_timeout_s == 15


def test_wired_into_tools_config():
    tools = ToolsConfig()
    assert isinstance(tools.location, LocationConfig)
    assert Config().tools.location.enable is True


def test_camel_case_aliases_load():
    # Base genera alias camelCase: un config.json può usare l'una o l'altra forma.
    cfg = LocationConfig.model_validate(
        {"enable": False, "telegramTtlS": 120, "freshTimeoutS": 5}
    )
    assert cfg.enable is False
    assert cfg.telegram_ttl_s == 120
    assert cfg.fresh_timeout_s == 5


def test_bounds_enforced():
    import pytest

    with pytest.raises(Exception):
        LocationConfig(telegram_ttl_s=1)  # < 60
    with pytest.raises(Exception):
        LocationConfig(fresh_timeout_s=999)  # > 60
