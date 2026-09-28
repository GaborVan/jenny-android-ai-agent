"""Il vocale su Telegram: scaricato, trascritto, consegnato come testo.

È l'unico tipo di update dove il contenuto utile non è nel payload ma va
*prodotto*: il file si scarica, si trascrive con il motore di sistema, e al
posto del segnaposto «🎤 voice message» l'agente riceve il testo. Fuori da
Android (host, test) la trascrizione non è disponibile e il vocale viaggia come
prima — è il *passthrough*, ed è la ragione per cui la prova storica
``test_voice_message_downloads_as_ogg`` resta verde senza modifiche.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.bus.queue import MessageBus
from jenny.channels import telegram_media
from jenny.channels.telegram import TelegramChannel
from jenny.config.schema import TelegramConfig


class FakeAPI:
    """Doppio minimo del client Bot API per il percorso vocale."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.file_calls: list[str] = []
        self.download_calls: list[str] = []

    async def get_file(self, file_id: str) -> dict[str, Any]:
        self.file_calls.append(file_id)
        return {"file_id": file_id, "file_path": f"voice/{file_id}.oga"}

    async def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        self.download_calls.append(file_path)
        return b"ogg-opus-bytes"

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None):
        self.sent.append((chat_id, text, parse_mode))
        return {"message_id": len(self.sent)}

    async def close(self) -> None:
        return None


def _channel(
    *, language: str = "uk", api: FakeAPI | None = None
) -> tuple[TelegramChannel, FakeAPI, MessageBus]:
    config = TelegramConfig(enabled=True, bot_token="TOKEN", paired_chat_id="42")
    bus = MessageBus()
    api = api or FakeAPI()
    return TelegramChannel(config, bus, api=api, language=language), api, bus


def _voice_update(chat_id: str = "42") -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": chat_id, "username": "utente"},
            "voice": {"file_id": "v1", "duration": 3},
        },
    }


def _voice_config(*, enable: bool = True, prefer_offline: bool = False) -> Any:
    """Una config ``voice`` finta, come la vedrebbe il canale."""

    async def _load() -> Any:  # pragma: no cover - sostituita sotto
        raise AssertionError

    return SimpleNamespace(voice=SimpleNamespace(enable=enable, prefer_offline=prefer_offline))


def _patch_config(monkeypatch: Any, config: Any) -> None:
    import jenny.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda: config)


def _patch_transcribe(monkeypatch: Any, result: Any, seen: list[dict[str, Any]] | None = None):
    async def _fake(path: str, *, language: str = "", prefer_offline: bool = False) -> Any:
        if seen is not None:
            seen.append({"path": path, "language": language, "prefer_offline": prefer_offline})
        return result

    import jenny.runtime.stt as stt

    monkeypatch.setattr(stt, "transcribe_file", _fake)


@pytest.fixture(autouse=True)
def _media_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    return tmp_path


# ── Trascrizione riuscita ──────────────────────────────────────────────────


async def test_a_voice_note_reaches_the_agent_as_text(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())
    _patch_transcribe(monkeypatch, {"ok": True, "text": "Нагадай мені завтра о дев'ятій"})
    ch, api, bus = _channel()

    await ch._handle_update(_voice_update())

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "Нагадай мені завтра о дев'ятій"
    # Il file resta allegato: la trascrizione non sostituisce l'audio.
    assert Path(msg.media[0]).suffix == ".ogg"
    assert api.sent == []


async def test_the_app_language_and_the_offline_flag_are_forwarded(monkeypatch: Any) -> None:
    seen: list[dict[str, Any]] = []
    _patch_config(monkeypatch, _voice_config(prefer_offline=True))
    _patch_transcribe(monkeypatch, {"ok": True, "text": "ciao"}, seen)
    ch, _, bus = _channel(language="it")

    await ch._handle_update(_voice_update())

    await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert seen == [
        {
            "path": seen[0]["path"],
            "language": "it",
            "prefer_offline": True,
        }
    ]
    assert seen[0]["path"].endswith(".ogg")


# ── Trascrizione fallita: riga di servizio, nessun turno ───────────────────


async def test_a_failed_transcription_replies_and_sends_no_turn(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())
    _patch_transcribe(
        monkeypatch,
        {"ok": False, "error": "stt_file_source_unsupported", "hint": "needs Android 13"},
    )
    ch, api, bus = _channel()

    await ch._handle_update(_voice_update())

    assert len(api.sent) == 1
    assert "голосове" in api.sent[0][1]
    assert bus.inbound.empty()


async def test_an_empty_transcript_is_a_failure(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())
    _patch_transcribe(monkeypatch, {"ok": True, "text": "   "})
    ch, api, bus = _channel()

    await ch._handle_update(_voice_update())

    assert len(api.sent) == 1
    assert bus.inbound.empty()


async def test_the_error_string_follows_the_channel_language(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())
    _patch_transcribe(monkeypatch, {"ok": False, "error": "stt_unavailable"})
    ch, api, _bus = _channel(language="en")

    await ch._handle_update(_voice_update())

    assert "couldn't transcribe" in api.sent[0][1]


# ── Passthrough: non si è tentato nulla ────────────────────────────────────


async def test_a_disabled_voice_section_delivers_the_file_unchanged(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config(enable=False))

    async def _never(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - non deve girare
        raise AssertionError("transcription must not be attempted")

    import jenny.runtime.stt as stt

    monkeypatch.setattr(stt, "transcribe_file", _never)
    ch, _api, bus = _channel()

    await ch._handle_update(_voice_update())

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "🎤 voice message"
    assert Path(msg.media[0]).suffix == ".ogg"


async def test_an_unreadable_config_is_a_passthrough(monkeypatch: Any) -> None:
    import jenny.config.loader as loader

    def _boom() -> Any:
        raise RuntimeError("no config")

    monkeypatch.setattr(loader, "load_config", _boom)

    async def _never(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - non deve girare
        raise AssertionError("transcription must not be attempted")

    import jenny.runtime.stt as stt

    monkeypatch.setattr(stt, "transcribe_file", _never)
    ch, _api, bus = _channel()

    await ch._handle_update(_voice_update())

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "🎤 voice message"


async def test_outside_android_the_none_result_is_a_passthrough(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())
    _patch_transcribe(monkeypatch, None)
    ch, api, bus = _channel()

    await ch._handle_update(_voice_update())

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "🎤 voice message"
    assert api.sent == []


# ── Gli altri media non passano dal riconoscimento ─────────────────────────


async def test_an_audio_track_is_not_transcribed(monkeypatch: Any) -> None:
    _patch_config(monkeypatch, _voice_config())

    async def _never(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - non deve girare
        raise AssertionError("music is not a voice note")

    import jenny.runtime.stt as stt

    monkeypatch.setattr(stt, "transcribe_file", _never)
    ch, _api, bus = _channel()

    update = {
        "update_id": 1,
        "message": {
            "chat": {"id": "42"},
            "from": {"id": "42"},
            "audio": {"file_id": "a1", "file_name": "song.mp3"},
        },
    }
    await ch._handle_update(update)

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "🎵 song.mp3"
