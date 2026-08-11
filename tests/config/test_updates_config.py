"""Test per ``config.updates`` (UpdatesConfig).

La sezione è nuova e opzionale: il caso che conta davvero è l'ultimo, cioè un
``config.json`` scritto da una versione precedente, che della sezione non sa
niente e deve continuare a caricarsi con i default.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jenny.config.schema import Config, UpdatesConfig
from jenny.pydantic_compat import ValidationError
from jenny.runtime.update_check import DEFAULT_MANIFEST_URL


def test_updates_config_defaults() -> None:
    cfg = UpdatesConfig()

    assert cfg.enabled is True
    assert cfg.manifest_url == DEFAULT_MANIFEST_URL
    assert cfg.check_interval_h == 24
    assert cfg.notify_in_chat is True


def test_root_config_carries_updates() -> None:
    config = Config()

    assert isinstance(config.updates, UpdatesConfig)
    assert config.updates.manifest_url.startswith("https://")


def test_updates_config_reads_camel_case_input() -> None:
    cfg = UpdatesConfig(
        **{
            "enabled": False,
            "manifestUrl": "https://example.com/latest.json",
            "checkIntervalH": 6,
            "notifyInChat": False,
        }
    )

    assert cfg.enabled is False
    assert cfg.manifest_url == "https://example.com/latest.json"
    assert cfg.check_interval_h == 6
    assert cfg.notify_in_chat is False


def test_updates_config_reads_snake_case_input() -> None:
    cfg = UpdatesConfig(**{"check_interval_h": 12, "notify_in_chat": False})

    assert cfg.check_interval_h == 12
    assert cfg.notify_in_chat is False


def test_updates_config_dump_uses_camel_case_aliases() -> None:
    dumped = UpdatesConfig(check_interval_h=48).model_dump(by_alias=True)

    assert dumped["checkIntervalH"] == 48
    assert dumped["manifestUrl"] == DEFAULT_MANIFEST_URL
    assert dumped["notifyInChat"] is True


def test_updates_config_round_trips_through_camel_case() -> None:
    original = UpdatesConfig(enabled=False, check_interval_h=72)

    assert UpdatesConfig(**original.model_dump(by_alias=True)) == original


@pytest.mark.parametrize("hours", [0, -1, 169])
def test_an_absurd_interval_is_rejected(hours: int) -> None:
    with pytest.raises(ValidationError):
        UpdatesConfig(check_interval_h=hours)


@pytest.mark.parametrize("hours", [1, 24, 168])
def test_interval_boundaries_are_accepted(hours: int) -> None:
    assert UpdatesConfig(check_interval_h=hours).check_interval_h == hours


def test_the_manifest_url_has_a_single_definition() -> None:
    """Lo schema e l'updater devono leggere la *stessa* costante, non due copie."""
    from jenny.runtime import update_check, update_manifest

    assert UpdatesConfig().manifest_url is update_manifest.DEFAULT_MANIFEST_URL
    assert update_check.DEFAULT_MANIFEST_URL is update_manifest.DEFAULT_MANIFEST_URL


def test_importing_the_schema_does_not_drag_in_the_updater() -> None:
    """``config/bootstrap.py`` gira prima dell'event loop: l'import pesa davvero.

    Lo schema nominava il default del manifest importandolo dall'updater, e con
    esso si portava dietro ``httpx`` (più ``runtime.context`` e
    ``security.network``) a import-time, su Chaquopy, a ogni avvio — per una
    stringa. L'interprete separato serve perché in-process quei moduli sono già
    stati importati da qualcun altro.
    """
    probe = (
        "import sys, jenny.config.schema;"
        "print(','.join(m for m in ('httpx', 'jenny.runtime.update_check')"
        " if m in sys.modules))"
    )
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"unexpected imports: {result.stdout.strip()}"
    """Nessuna migrazione: chi aggiorna non ha la sezione e prende i default."""
    legacy = {
        "configVersion": 1,
        "agents": {"defaults": {"model": "gpt-5", "language": "en"}},
        "gateway": {"port": 18790},
    }

    config = Config(**legacy)

    assert config.updates == UpdatesConfig()
    assert config.agents.defaults.language == "en"
