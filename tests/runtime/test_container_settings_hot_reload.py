"""Hot-reload dei parametri di generazione in ``GatewayContainer``.

La guardia di ``_on_settings_changed`` scartava l'aggiornamento quando modello e
api_base erano identici — cioè esattamente il caso di un cambio di
``max_tokens`` / ``temperature`` / ``reasoning_effort``, che lasciava scritto il
config e inerte il provider fino al riavvio.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jenny.config.schema import Config
from jenny.providers.base import GenerationSettings
from jenny.runtime.container import GatewayContainer


def _provider(model: str, generation: GenerationSettings, api_base: str = "https://api.test"):
    return SimpleNamespace(api_base=api_base, generation=generation, default_model=model)


@pytest.fixture
def container_with_agent(monkeypatch: pytest.MonkeyPatch):
    """Container con un agente finto e le dipendenze di reload sostituite."""
    container = GatewayContainer.__new__(GatewayContainer)
    old_generation = GenerationSettings(temperature=0.1, max_tokens=8192)
    agent = SimpleNamespace(
        model="deepseek-v4-flash",
        provider=_provider("deepseek-v4-flash", old_generation),
        _apply_provider_switch=MagicMock(),
    )
    container._agent = agent
    return container, agent


def _patch_reload(monkeypatch: pytest.MonkeyPatch, *, model: str, generation: GenerationSettings):
    # I moduli si importano qui e si patcha l'oggetto, non il target come stringa:
    # ``_on_settings_changed`` importa entrambi dentro la funzione, quindi con la
    # forma a stringa il test passa o falla in base a chi ha già importato
    # ``jenny.providers.factory`` prima di lui.
    from jenny.config import loader as config_loader
    from jenny.providers import factory as provider_factory

    config = Config()
    config.agents.defaults.model = model
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        provider_factory, "make_provider", lambda *a, **k: _provider(model, generation)
    )


def test_generation_change_is_applied(container_with_agent, monkeypatch) -> None:
    container, agent = container_with_agent
    _patch_reload(
        monkeypatch,
        model="deepseek-v4-flash",  # invariato: solo i parametri cambiano
        generation=GenerationSettings(temperature=0.1, max_tokens=16384),
    )

    container._on_settings_changed()

    agent._apply_provider_switch.assert_called_once()
    assert agent._apply_provider_switch.call_args.kwargs["publish_update"] is False


def test_identical_settings_still_short_circuit(container_with_agent, monkeypatch) -> None:
    """La guardia resta: senza differenze non si ricostruisce niente."""
    container, agent = container_with_agent
    _patch_reload(
        monkeypatch,
        model="deepseek-v4-flash",
        generation=GenerationSettings(temperature=0.1, max_tokens=8192),
    )

    container._on_settings_changed()

    agent._apply_provider_switch.assert_not_called()


def test_model_change_still_publishes_the_switch(container_with_agent, monkeypatch) -> None:
    """Un cambio di modello va annunciato; uno dei soli parametri no."""
    container, agent = container_with_agent
    _patch_reload(
        monkeypatch,
        model="other-model",
        generation=GenerationSettings(temperature=0.1, max_tokens=8192),
    )

    container._on_settings_changed()

    agent._apply_provider_switch.assert_called_once()
    assert agent._apply_provider_switch.call_args.kwargs["publish_update"] is True
