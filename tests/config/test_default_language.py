"""Lingua automatica del dispositivo e persistenza della sentinella."""

import json

import pytest

from jenny.config.loader import _resolve_default_language, load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context


@pytest.mark.parametrize("locale, expected", [("uk", "uk"), ("de", "it"), (None, "it")])
def test_resolve_default_language(monkeypatch, locale, expected):
    monkeypatch.setattr(get_runtime_context(), "device_locale", locale)
    config = Config()
    _resolve_default_language(config)
    assert config.agents.defaults.language == expected


def test_explicit_language_is_preserved(monkeypatch):
    monkeypatch.setattr(get_runtime_context(), "device_locale", "uk")
    config = Config.model_validate({"agents": {"defaults": {"language": "en"}}})
    _resolve_default_language(config)
    assert config.agents.defaults.language == "en"


def test_legacy_language_loader_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(get_runtime_context(), "device_locale", "uk")
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "configVersion": 1,
        "agents": {"defaults": {"language": "it"}},
    }), encoding="utf-8")
    config = load_config(path)
    assert config.agents.defaults.language == "uk"
    save_config(config, path)
    assert json.loads(path.read_text(encoding="utf-8"))["agents"]["defaults"]["language"] == ""
    assert load_config(path).agents.defaults.language == "uk"
