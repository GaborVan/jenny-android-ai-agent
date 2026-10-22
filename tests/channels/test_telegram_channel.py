"""Test per ``jenny.channels.telegram.TelegramChannel``: pairing, allow-list,
media non gestiti, publish inbound e consegna outbound.

Il canale è pura consegna: la proiezione dei turni sulla vista WebUI
(user echo, mirror del finale, turn_end) è responsabilità del runtime ed è
testata in ``tests/webui/test_webui_view_projection.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels import telegram_media
from jenny.channels.telegram import TelegramChannel
from jenny.channels.telegram_api import TelegramAPIError
from jenny.config.schema import TelegramConfig
from jenny.session.keys import UNIFIED_SESSION_KEY, session_key_for_channel


class FakeAPI:
    """Doppio del client Bot API: registra i send, opzionalmente rifiuta l'HTML."""

    def __init__(self, *, fail_html: bool = False, photo_400: bool = False) -> None:
        self.sent: list[tuple[str, str, str | None]] = []
        self.media: list[dict[str, Any]] = []
        self.fail_html = fail_html
        self.photo_400 = photo_400
        self.closed = False
        # Doppi per il download inbound (getFile/download_file): il chiamante
        # dei test popola file_info/file_bytes per file_id, o file_errors per
        # simulare un getFile rifiutato.
        self.file_calls: list[str] = []
        self.download_calls: list[tuple[str, int]] = []
        self.file_info: dict[str, dict[str, Any]] = {}
        self.file_bytes: dict[str, bytes] = {}
        self.file_errors: dict[str, TelegramAPIError] = {}

    async def get_file(self, file_id: str) -> dict[str, Any]:
        self.file_calls.append(file_id)
        if file_id in self.file_errors:
            raise self.file_errors[file_id]
        return self.file_info.get(file_id, {"file_id": file_id, "file_path": f"docs/{file_id}"})

    async def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        self.download_calls.append((file_path, max_bytes))
        data = self.file_bytes.get(file_path, b"fake-bytes")
        if len(data) > max_bytes:
            raise TelegramAPIError(413, f"file exceeds {max_bytes} bytes: {file_path}")
        return data

    async def send_message(self, chat_id: str, text: str, *, parse_mode: str | None = None):
        if parse_mode == "HTML" and self.fail_html:
            raise TelegramAPIError(400, "can't parse entities")
        self.sent.append((chat_id, text, parse_mode))
        return {"message_id": len(self.sent)}

    async def send_media_file(
        self, chat_id: str, *, method: str, field: str, filename: str,
        data: bytes, caption: str | None = None, parse_mode: str | None = None,
    ):
        if self.photo_400 and method == "sendPhoto":
            raise TelegramAPIError(400, "IMAGE_PROCESS_FAILED")
        self.media.append({
            "kind": "file", "method": method, "field": field,
            "filename": filename, "size": len(data),
        })
        return {"message_id": len(self.media)}

    async def send_media_url(
        self, chat_id: str, *, method: str, field: str, url: str,
        caption: str | None = None, parse_mode: str | None = None,
    ):
        self.media.append({"kind": "url", "method": method, "field": field, "url": url})
        return {"message_id": len(self.media)}

    async def get_updates(self, offset, timeout_s):  # pragma: no cover - non usato qui
        return []

    async def close(self) -> None:
        self.closed = True


def _channel(
    *,
    paired: str | None = None,
    pairing_code: str | None = None,
    api: FakeAPI | None = None,
    on_paired=None,
) -> tuple[TelegramChannel, FakeAPI, MessageBus]:
    config = TelegramConfig(
        enabled=True,
        bot_token="TOKEN",
        paired_chat_id=paired,
        pairing_code=pairing_code,
    )
    bus = MessageBus()
    api = api or FakeAPI()
    ch = TelegramChannel(config, bus, api=api, on_paired=on_paired, language="en")
    return ch, api, bus


def _update(chat_id: str, *, text: str | None = None, **extra) -> dict[str, Any]:
    message: dict[str, Any] = {
        "chat": {"id": chat_id},
        "from": {"id": chat_id, "username": "utente"},
        **extra,
    }
    if text is not None:
        message["text"] = text
    return {"update_id": 1, "message": message}


# --- pairing -------------------------------------------------------------------


async def test_pairing_with_correct_code() -> None:
    paired_calls: list[tuple[str, str | None]] = []
    ch, api, bus = _channel(
        pairing_code="123456", on_paired=lambda c, u: paired_calls.append((c, u))
    )

    await ch._handle_update(_update("42", text="123456"))

    assert ch.paired_chat_id == "42"
    assert paired_calls == [("42", "utente")]
    # Un solo messaggio combinato: conferma + benvenuto.
    assert len(api.sent) == 1
    assert "Paired" in api.sent[0][1]
    assert "/new" in api.sent[0][1]
    assert bus.inbound.empty()  # il codice non raggiunge l'agente


async def test_deep_link_start_with_code_pairs() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("42", text="/start 123456"))
    assert ch.paired_chat_id == "42"
    assert len(api.sent) == 1 and "Paired" in api.sent[0][1]
    assert bus.inbound.empty()


async def test_start_case_insensitive_with_bot_suffix_pairs() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("42", text="/START@JennyBot 123456"))
    assert ch.paired_chat_id == "42"


async def test_bare_start_during_pairing_gets_prompt() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("42", text="/start"))
    assert ch.paired_chat_id is None
    assert len(api.sent) == 1
    assert "6-digit code" in api.sent[0][1]
    assert bus.inbound.empty()


async def test_wrong_code_gets_feedback() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("42", text="000000"))
    assert ch.paired_chat_id is None
    assert len(api.sent) == 1
    assert "Invalid code" in api.sent[0][1]
    assert bus.inbound.empty()


async def test_wrong_start_payload_gets_feedback() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("42", text="/start 000000"))
    assert ch.paired_chat_id is None
    assert api.sent and "Invalid code" in api.sent[0][1]


async def test_silence_when_no_pairing_code() -> None:
    """No-oracle: senza finestra di pairing il bot resta completamente muto."""
    ch, api, bus = _channel()  # né paired né pairing_code
    await ch._handle_update(_update("42", text="/start"))
    await ch._handle_update(_update("42", text="ciao?"))
    assert api.sent == []
    assert bus.inbound.empty()


async def test_throttle_caps_replies_then_silence() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    for _ in range(6):
        await ch._handle_update(_update("42", text="000000"))
    assert len(api.sent) == 5  # il sesto tentativo non riceve nulla
    assert ch.paired_chat_id is None


async def test_capped_chat_cannot_pair_even_with_correct_code() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    for _ in range(5):
        await ch._handle_update(_update("42", text="000000"))
    api.sent.clear()
    await ch._handle_update(_update("42", text="123456"))
    assert ch.paired_chat_id is None  # ineleggibile: il throttle blocca il pairing
    assert api.sent == []


async def test_throttle_is_per_chat() -> None:
    ch, api, bus = _channel(pairing_code="123456")
    for _ in range(5):
        await ch._handle_update(_update("666", text="000000"))
    await ch._handle_update(_update("42", text="123456"))
    assert ch.paired_chat_id == "42"


async def test_tracked_chats_bound_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr("jenny.channels.telegram._MAX_TRACKED_CHATS", 1)
    ch, api, bus = _channel(pairing_code="123456")
    await ch._handle_update(_update("666", text="000000"))  # occupa l'unico slot
    api.sent.clear()
    # Chat nuova a tabella piena: nessuna risposta e niente pairing (fail-closed).
    await ch._handle_update(_update("42", text="/start"))
    await ch._handle_update(_update("42", text="123456"))
    assert ch.paired_chat_id is None
    assert api.sent == []


async def test_stranger_ignored_after_pairing() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("666", text="ciao"))
    assert api.sent == []
    assert bus.inbound.empty()


async def test_stranger_start_while_paired_is_silent() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("666", text="/start"))
    assert api.sent == []
    assert bus.inbound.empty()


async def test_owner_start_gets_welcome_not_forwarded() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", text="/start"))
    await ch._handle_update(_update("42", text="/start 123456"))
    assert len(api.sent) == 2
    assert all("/new" in text for _, text, _ in api.sent)
    assert bus.inbound.empty()  # guida rapida di servizio, non un turno LLM


async def test_owner_new_still_forwarded() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", text="/new"))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "/new"
    assert api.sent == []


# --- inbound -------------------------------------------------------------------


async def test_owner_text_published_on_bus_unified_session() -> None:
    ch, api, bus = _channel(paired="42")

    await ch._handle_update(_update("42", text="ciao jenny"))

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.channel == "telegram"
    assert msg.content == "ciao jenny"
    assert msg.session_key_override is None
    # La chiave canale collassa comunque sulla sessione unificata.
    assert session_key_for_channel(msg.channel, msg.chat_id) == UNIFIED_SESSION_KEY
    # Il turn id viaggia nei metadata per correlare le righe della vista WebUI.
    turn_id = msg.metadata.get("webui_turn_id")
    assert isinstance(turn_id, str) and turn_id


async def test_each_inbound_gets_distinct_turn_id() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", text="uno"))
    await ch._handle_update(_update("42", text="due"))
    first = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    second = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert first.metadata["webui_turn_id"] != second.metadata["webui_turn_id"]


async def test_photo_message_downloads_and_publishes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["abc"] = {"file_id": "abc", "file_path": "photos/abc.jpg", "file_size": 5}
    api.file_bytes["photos/abc.jpg"] = b"\x89PNGfake"
    ch, api, bus = _channel(paired="42", api=api)

    await ch._handle_update(
        _update(
            "42",
            photo=[{"file_id": "abc", "file_size": 5, "width": 10, "height": 10}],
            caption="guarda qua",
        )
    )

    assert api.sent == []  # nessuna risposta di servizio: il download è riuscito
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "guarda qua"
    saved = Path(msg.media[0])
    assert saved.parent == tmp_path
    assert saved.suffix == ".jpg"
    assert saved.read_bytes() == b"\x89PNGfake"


async def test_photo_without_caption_gets_default_content(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    ch, api, bus = _channel(paired="42")

    await ch._handle_update(_update("42", photo=[{"file_id": "abc", "file_size": 5}]))

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "📷 photo"


async def test_document_message_uses_filename_for_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["doc1"] = {"file_id": "doc1", "file_path": "documents/file_1.bin"}
    api.file_bytes["documents/file_1.bin"] = b"pdf-bytes"
    ch, api, bus = _channel(paired="42", api=api)

    await ch._handle_update(_update("42", document={"file_id": "doc1", "file_name": "invoice.pdf"}))

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "📄 invoice.pdf"
    saved = Path(msg.media[0])
    assert saved.suffix == ".pdf"
    assert saved.read_bytes() == b"pdf-bytes"


async def test_voice_message_downloads_as_ogg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    ch, api, bus = _channel(paired="42")

    await ch._handle_update(_update("42", voice={"file_id": "v1"}))

    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "🎤 voice message"
    assert Path(msg.media[0]).suffix == ".ogg"


async def test_media_too_large_is_skipped_without_reply(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["big"] = {
        "file_id": "big",
        "file_path": "video/big.mp4",
        "file_size": telegram_media.MAX_DOWNLOAD_BYTES + 1,
    }
    ch, api, bus = _channel(paired="42", api=api)

    await ch._handle_update(_update("42", video={"file_id": "big"}))

    # Download rifiutato per dimensione: nessun turno pubblicato e nessuna
    # risposta "non supportato" (il tipo *e* gestito, solo il file e troppo
    # grande), niente file scritto sul disco.
    assert bus.inbound.empty()
    assert api.sent == []
    assert api.download_calls == []  # skip prima ancora di scaricare
    assert list(tmp_path.iterdir()) == []


async def test_download_over_cap_streamed_is_skipped(tmp_path, monkeypatch) -> None:
    """Il cap vale anche senza ``file_size`` dichiarato (streaming interrotto)."""
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["big2"] = {"file_id": "big2", "file_path": "video/big2.mp4"}
    api.file_bytes["video/big2.mp4"] = b"x" * (telegram_media.MAX_DOWNLOAD_BYTES + 1)
    ch, api, bus = _channel(paired="42", api=api)

    await ch._handle_update(_update("42", video={"file_id": "big2"}))

    assert bus.inbound.empty()
    assert api.sent == []
    assert list(tmp_path.iterdir()) == []


async def test_get_file_error_is_swallowed_without_reply(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(telegram_media, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_errors["missing"] = TelegramAPIError(400, "file not found")
    ch, api, bus = _channel(paired="42", api=api)

    await ch._handle_update(_update("42", audio={"file_id": "missing"}))

    assert bus.inbound.empty()
    assert api.sent == []


async def test_sticker_with_emoji_becomes_text() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", sticker={"file_id": "s1", "emoji": "😂"}))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert msg.content == "😂"
    assert msg.media == []
    assert api.sent == []


async def test_sticker_without_emoji_gets_unsupported_reply() -> None:
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", sticker={"file_id": "s1"}))
    assert len(api.sent) == 1
    assert "isn't supported yet" in api.sent[0][1]
    assert bus.inbound.empty()


async def test_unsupported_media_type_gets_reply() -> None:
    """Poll non porta un file scaricabile: resta sulla fallback generica."""
    ch, api, bus = _channel(paired="42")
    await ch._handle_update(_update("42", poll={"id": "1", "question": "?"}))
    assert len(api.sent) == 1
    assert "isn't supported yet" in api.sent[0][1]
    assert bus.inbound.empty()


# --- outbound ------------------------------------------------------------------


async def test_send_formats_html() -> None:
    ch, api, bus = _channel(paired="42")

    pending = await ch.send(
        OutboundMessage(channel="telegram", chat_id="42", content="**ciao**")
    )

    assert pending == []
    assert api.sent == [("42", "<b>ciao</b>", "HTML")]


async def test_send_falls_back_to_plain_text_on_400() -> None:
    ch, api, bus = _channel(paired="42", api=FakeAPI(fail_html=True))
    await ch.send(OutboundMessage(channel="telegram", chat_id="42", content="**x**"))
    assert api.sent == [("42", "**x**", None)]


async def test_mirror_copy_still_delivered() -> None:
    """Le copie ``_mirror`` del fan-out proattivo vanno consegnate su Telegram."""
    ch, api, bus = _channel(paired="42")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42", content="promemoria",
            metadata={"_mirror": True},
        )
    )
    assert api.sent == [("42", "promemoria", "HTML")]


async def test_send_raster_media_as_photo(tmp_path) -> None:
    ch, api, bus = _channel(paired="42")
    img = tmp_path / "gatto.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42", content="ecco qua", media=[str(img)]
        )
    )
    # Prima il testo, poi la foto.
    assert api.sent == [("42", "ecco qua", "HTML")]
    assert len(api.media) == 1
    assert api.media[0]["method"] == "sendPhoto"
    assert api.media[0]["field"] == "photo"
    assert api.media[0]["filename"] == "gatto.png"


async def test_send_svg_media_as_document(tmp_path) -> None:
    ch, api, bus = _channel(paired="42")
    svg = tmp_path / "gatto.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    await ch.send(
        OutboundMessage(channel="telegram", chat_id="42", content="", media=[str(svg)])
    )
    # Nessun testo (content vuoto), solo il documento.
    assert api.sent == []
    assert len(api.media) == 1
    assert api.media[0]["method"] == "sendDocument"
    assert api.media[0]["field"] == "document"


async def test_send_media_url_referenced(tmp_path) -> None:
    ch, api, bus = _channel(paired="42")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42", content="",
            media=["https://example.com/gatto.jpg"],
        )
    )
    assert len(api.media) == 1
    assert api.media[0] == {
        "kind": "url", "method": "sendPhoto", "field": "photo",
        "url": "https://example.com/gatto.jpg",
    }


async def test_send_photo_400_falls_back_to_document(tmp_path) -> None:
    ch, api, bus = _channel(paired="42", api=FakeAPI(photo_400=True))
    img = tmp_path / "gatto.png"
    img.write_bytes(b"fake")
    await ch.send(
        OutboundMessage(channel="telegram", chat_id="42", content="", media=[str(img)])
    )
    # sendPhoto rifiutato (400) → ricade su sendDocument.
    assert len(api.media) == 1
    assert api.media[0]["method"] == "sendDocument"


async def test_send_missing_media_file_skipped(tmp_path) -> None:
    ch, api, bus = _channel(paired="42")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42", content="testo",
            media=[str(tmp_path / "inesistente.png")],
        )
    )
    # Il testo passa, il media mancante viene saltato senza errori.
    assert api.sent == [("42", "testo", "HTML")]
    assert api.media == []


async def test_turn_end_is_ignored() -> None:
    """Il turn_end è un evento della vista WebUI: su Telegram non si invia nulla."""
    ch, api, bus = _channel(paired="42")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42", content="",
            metadata={"_turn_end": True, "webui_turn_id": "turn-test"},
        )
    )
    assert api.sent == []


async def test_webui_only_events_are_ignored() -> None:
    ch, api, bus = _channel(paired="42")
    for key in ("_progress", "_session_updated", "_stream_delta", "_user_echo"):
        await ch.send(
            OutboundMessage(
                channel="telegram", chat_id="42", content="x", metadata={key: True}
            )
        )
    assert api.sent == []


async def test_unpaired_outbound_dropped() -> None:
    ch, api, bus = _channel()
    await ch.send(OutboundMessage(channel="telegram", chat_id="42", content="ciao"))
    assert api.sent == []


async def test_long_message_chunked_in_order() -> None:
    ch, api, bus = _channel(paired="42")
    await ch.send(
        OutboundMessage(
            channel="telegram", chat_id="42",
            content=("a" * 3000) + "\n\n" + ("b" * 3000),
        )
    )
    assert len(api.sent) == 2
    assert api.sent[0][1].startswith("a")
    assert api.sent[1][1].startswith("b")


async def test_stop_closes_api_client() -> None:
    ch, api, bus = _channel(paired="42")
    await ch.stop()
    assert api.closed is True
