"""Persistenza dei parametri di generazione dalle impostazioni WebUI.

Regressione: la UI agganciava ``max_tokens`` / ``temperature`` /
``reasoning_effort`` all'auto-save, ma ``update_agent_settings`` non aveva un
ramo per nessuno dei tre. La richiesta arrivava, ``changed`` restava False,
niente veniva scritto — e il client mostrava "Saved!" perché la risposta è 200
comunque. Tre campi decorativi: il valore tornava al precedente al riavvio.
"""

from __future__ import annotations

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import WebUISettingsError, update_agent_settings


@pytest.fixture
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    save_config(Config(), path)
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


# --- max_tokens ----------------------------------------------------------------


def test_max_tokens_is_persisted(config_path) -> None:
    payload = update_agent_settings({"max_tokens": ["16384"]})

    assert payload["agent"]["max_tokens"] == 16384
    assert load_config(config_path).agents.defaults.max_tokens == 16384


def test_max_tokens_accepts_camel_case_alias(config_path) -> None:
    update_agent_settings({"maxTokens": ["4096"]})

    assert load_config(config_path).agents.defaults.max_tokens == 4096


def test_max_tokens_rejects_non_integer(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="max_tokens must be an integer"):
        update_agent_settings({"max_tokens": ["lots"]})


def test_max_tokens_rejects_zero(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="max_tokens must be at least 1"):
        update_agent_settings({"max_tokens": ["0"]})


# --- temperature ---------------------------------------------------------------


def test_temperature_is_persisted(config_path) -> None:
    update_agent_settings({"temperature": ["0.7"]})

    assert load_config(config_path).agents.defaults.temperature == 0.7


def test_temperature_accepts_comma_decimal_separator(config_path) -> None:
    """Un ``input type=number`` su locale italiano può mandare la virgola."""
    update_agent_settings({"temperature": ["0,4"]})

    assert load_config(config_path).agents.defaults.temperature == 0.4


def test_temperature_rejects_out_of_range(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="temperature must be between 0 and 2"):
        update_agent_settings({"temperature": ["3"]})


def test_temperature_zero_is_persisted(config_path) -> None:
    """0.0 è un valore legittimo, non un campo vuoto da ignorare."""
    save_config(Config(), config_path)
    update_agent_settings({"temperature": ["0"]})

    assert load_config(config_path).agents.defaults.temperature == 0.0


# --- reasoning_effort ----------------------------------------------------------


def test_reasoning_effort_is_persisted(config_path) -> None:
    update_agent_settings({"reasoning_effort": ["medium"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "medium"


def test_reasoning_effort_empty_clears_it(config_path) -> None:
    """L'opzione "—" della select significa "lascia decidere al provider"."""
    update_agent_settings({"reasoning_effort": ["high"]})
    update_agent_settings({"reasoning_effort": [""]})

    assert load_config(config_path).agents.defaults.reasoning_effort is None


def test_reasoning_effort_survives_an_unrelated_update(config_path) -> None:
    """Campo assente != campo azzerato.

    Senza la sentinella, salvare un altro campo azzererebbe l'effort come
    effetto collaterale.
    """
    update_agent_settings({"reasoning_effort": ["high"]})
    update_agent_settings({"max_tokens": ["8192"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "high"


def test_reasoning_effort_rejects_unknown_value(config_path) -> None:
    with pytest.raises(WebUISettingsError, match="reasoning_effort must be one of"):
        update_agent_settings({"reasoning_effort": ["turbo"]})


def test_reasoning_effort_accepts_provider_vocabulary(config_path) -> None:
    """L'API accetta tutto ciò che il provider sa normalizzare, non solo la select."""
    update_agent_settings({"reasoning_effort": ["minimum"]})

    assert load_config(config_path).agents.defaults.reasoning_effort == "minimum"
