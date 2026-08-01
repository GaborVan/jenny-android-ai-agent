"""Test per la risoluzione della timezone di default in load/save_config.

La sentinella "" (= auto) viene risolta una sola volta in ``load_config``
nella timezone del device (se rilevata) o UTC; ``save_config`` riporta a ""
il valore che coincide con la timezone del device, così la modalità auto
resta appiccicosa attraverso i salvataggi.
"""

import json

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context


@pytest.fixture
def config_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    return path


def _set_device_tz(monkeypatch: pytest.MonkeyPatch, tz: str | None) -> None:
    monkeypatch.setattr(get_runtime_context(), "device_timezone", tz)


def test_auto_resolves_to_device_timezone(config_path, monkeypatch) -> None:
    _set_device_tz(monkeypatch, "Europe/Rome")

    config = load_config()

    assert config.agents.defaults.timezone == "Europe/Rome"


def test_auto_resolves_to_utc_without_device(config_path, monkeypatch) -> None:
    _set_device_tz(monkeypatch, None)

    config = load_config()

    assert config.agents.defaults.timezone == "UTC"


def test_explicit_timezone_is_preserved(config_path, monkeypatch) -> None:
    _set_device_tz(monkeypatch, "Europe/Rome")
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"timezone": "Asia/Shanghai"}}}),
        encoding="utf-8",
    )

    config = load_config()

    assert config.agents.defaults.timezone == "Asia/Shanghai"


def test_save_writes_auto_when_equal_to_device_tz(config_path, monkeypatch) -> None:
    _set_device_tz(monkeypatch, "Europe/Rome")
    config = load_config()  # risolve "" -> Europe/Rome

    save_config(config)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["agents"]["defaults"]["timezone"] == ""
    # E il round-trip continua a seguire il device.
    assert load_config().agents.defaults.timezone == "Europe/Rome"


def test_save_preserves_explicit_different_tz(config_path, monkeypatch) -> None:
    _set_device_tz(monkeypatch, "Europe/Rome")
    config = Config()
    config.agents.defaults.timezone = "Asia/Shanghai"

    save_config(config)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["agents"]["defaults"]["timezone"] == "Asia/Shanghai"
