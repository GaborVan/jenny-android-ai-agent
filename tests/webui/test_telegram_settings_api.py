"""Test per ``jenny.webui.telegram_api``: masking del token, salvataggio con
validazione getMe, unpair/disable e persistenza del pairing."""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.runtime.context import get_runtime_context
from jenny.webui.settings_api import WebUISettingsError
from jenny.webui.telegram_api import (
    disable_telegram,
    record_paired,
    save_telegram_token,
    telegram_status_payload,
    unpair_telegram,
)

TOKEN = "123456789:AAtestTOKENtestTOKENtestTOKEN"


def _configure(tmp_path, monkeypatch, **telegram_fields) -> None:
    config = Config()
    for key, value in telegram_fields.items():
        setattr(config.telegram, key, value)
    config_path = tmp_path / "config.json"
    save_config(config, config_path)
    monkeypatch.setattr(get_runtime_context(), "config_path", config_path)


class FakeAPI:
    """Doppio di TelegramAPI per save_telegram_token (getMe + setMyCommands)."""

    fail: Exception | None = None
    fail_commands: Exception | None = None
    commands_calls: list[list[dict[str, str]]] = []

    def __init__(self, token: str, **kwargs) -> None:
        self.token = token

    async def get_me(self):
        if FakeAPI.fail is not None:
            raise FakeAPI.fail
        return {"username": "jenny_bot"}

    async def set_my_commands(self, commands):
        if FakeAPI.fail_commands is not None:
            raise FakeAPI.fail_commands
        FakeAPI.commands_calls.append(commands)
        return True

    async def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_api(monkeypatch):
    FakeAPI.fail = None
    FakeAPI.fail_commands = None
    FakeAPI.commands_calls = []
    monkeypatch.setattr("jenny.channels.telegram_api.TelegramAPI", FakeAPI)
    yield


# --- status ----------------------------------------------------------------


def test_status_masks_token(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch, enabled=True, bot_token=TOKEN, pairing_code="123456")
    payload = telegram_status_payload()
    assert payload["configured"] is True
    assert payload["token_hint"].startswith("1234")
    assert "..." in payload["token_hint"]
    assert TOKEN not in json.dumps(payload)
    assert payload["pairing_code"] == "123456"


def test_status_hides_code_when_paired(tmp_path, monkeypatch) -> None:
    _configure(
        tmp_path, monkeypatch,
        enabled=True, bot_token=TOKEN, paired_chat_id="42",
        paired_username="me", pairing_code="999999",
    )
    payload = telegram_status_payload()
    assert payload["paired"] is True
    assert payload["pairing_code"] is None
    assert payload["paired_username"] == "me"


# --- save token --------------------------------------------------------------


async def test_save_token_validates_and_generates_code(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    payload = await save_telegram_token(TOKEN)

    assert payload["enabled"] is True
    assert payload["bot_username"] == "jenny_bot"
    assert payload["paired"] is False
    assert len(payload["pairing_code"]) == 6

    config = load_config()
    assert config.telegram.bot_token == TOKEN
    assert config.telegram.enabled is True
    assert config.telegram.pairing_code == payload["pairing_code"]


async def test_save_new_token_resets_previous_pairing(tmp_path, monkeypatch) -> None:
    _configure(
        tmp_path, monkeypatch,
        enabled=True, bot_token="old", paired_chat_id="42", paired_username="me",
    )
    payload = await save_telegram_token(TOKEN)
    assert payload["paired"] is False
    config = load_config()
    assert config.telegram.paired_chat_id is None


async def test_save_rejected_token_raises(tmp_path, monkeypatch) -> None:
    from jenny.channels.telegram_api import TelegramAPIError

    _configure(tmp_path, monkeypatch)
    FakeAPI.fail = TelegramAPIError(401, "Unauthorized")
    with pytest.raises(WebUISettingsError, match="rejected"):
        await save_telegram_token(TOKEN)
    # Config intatta: niente stato zombie.
    assert load_config().telegram.bot_token is None


async def test_save_empty_token_raises(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    with pytest.raises(WebUISettingsError, match="required"):
        await save_telegram_token("   ")


async def test_save_token_registers_command_menu(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    await save_telegram_token(TOKEN)
    assert len(FakeAPI.commands_calls) == 1
    registered = {c["command"] for c in FakeAPI.commands_calls[0]}
    assert registered == {"start", "new"}


async def test_save_token_survives_set_my_commands_failure(tmp_path, monkeypatch) -> None:
    """Il menu comandi è best-effort: un fallimento non blocca il salvataggio."""
    _configure(tmp_path, monkeypatch)
    FakeAPI.fail_commands = RuntimeError("boom")
    payload = await save_telegram_token(TOKEN)
    assert payload["enabled"] is True
    assert load_config().telegram.bot_token == TOKEN


# --- pairing / unpair / disable ----------------------------------------------


async def test_record_paired_persists_and_clears_code(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch, enabled=True, bot_token=TOKEN, pairing_code="123456")
    await record_paired("42", "utente")
    config = load_config()
    assert config.telegram.paired_chat_id == "42"
    assert config.telegram.paired_username == "utente"
    assert config.telegram.pairing_code is None


async def test_unpair_regenerates_code(tmp_path, monkeypatch) -> None:
    _configure(
        tmp_path, monkeypatch,
        enabled=True, bot_token=TOKEN, paired_chat_id="42", paired_username="me",
    )
    payload = await unpair_telegram()
    assert payload["paired"] is False
    assert len(payload["pairing_code"]) == 6
    config = load_config()
    assert config.telegram.paired_chat_id is None
    assert config.telegram.pairing_code == payload["pairing_code"]


async def test_unpair_without_token_raises(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch)
    with pytest.raises(WebUISettingsError, match="not configured"):
        await unpair_telegram()


async def test_disable_keeps_token(tmp_path, monkeypatch) -> None:
    _configure(tmp_path, monkeypatch, enabled=True, bot_token=TOKEN)
    payload = await disable_telegram()
    assert payload["enabled"] is False
    config = load_config()
    assert config.telegram.bot_token == TOKEN
    assert config.telegram.enabled is False


# --- concorrenza col resto delle impostazioni ------------------------------


async def test_provider_added_during_pairing_is_not_lost(tmp_path, monkeypatch) -> None:
    """La regressione che ha motivato la 0.3.2.

    ``save_telegram_token`` faceva tre chiamate di rete tenendo in mano la
    config letta *prima*: qualunque impostazione salvata in quei secondi veniva
    riportata indietro dal suo salvataggio finale. Qui un provider viene
    aggiunto mentre il pairing è a metà rete; deve sopravvivere.
    """
    import asyncio

    from jenny.webui.settings_api import update_provider

    _configure(tmp_path, monkeypatch)
    provider_added = asyncio.Event()

    class SlowAPI(FakeAPI):
        async def get_me(self):
            # Mentre il pairing è in attesa della rete, un altro handler salva.
            await update_provider({
                "name": "local-llama",
                "format": "openai_compat",
                "api_key": "EMPTY",
                "api_base": "http://127.0.0.1:8080/v1",
            })
            provider_added.set()
            return {"username": "jenny_bot"}

    monkeypatch.setattr("jenny.channels.telegram_api.TelegramAPI", SlowAPI)

    payload = await save_telegram_token(TOKEN)

    assert provider_added.is_set()
    assert payload["configured"] is True
    config = load_config()
    assert config.telegram.bot_token == TOKEN
    assert [p.name for p in config.providers.providers] == ["local-llama"]
