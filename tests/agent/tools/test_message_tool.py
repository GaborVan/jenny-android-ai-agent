import os

import pytest

from jenny.agent.tools.message import MessageTool
from jenny.bus.events import OutboundMessage
from jenny.config.paths import get_workspace_path


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context() -> None:
    tool = MessageTool()
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "not a list",
        [["ok"], "row-not-a-list"],
        [["ok", 42]],
        [[None]],
    ],
)
async def test_message_tool_rejects_malformed_buttons(bad) -> None:
    """``buttons`` must be ``list[list[str]]``; the tool validates the shape
    up front so a malformed LLM payload errors visibly instead of slipping
    into the channel layer where it would be silently rejected."""
    tool = MessageTool()
    result = await tool.execute(
        content="hi",
        channel="websocket",
        chat_id="1",
        buttons=bad,
    )
    assert result == "Error: buttons must be a list of list of strings"


@pytest.mark.asyncio
async def test_message_tool_suppresses_delivery_when_active() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    token = tool.set_suppress_delivery(True)
    try:
        result = await tool.execute(content="all clear", channel="websocket", chat_id="1")
    finally:
        tool.reset_suppress_delivery(token)
    assert sent == []
    assert "not delivered" in result

    await tool.execute(content="real", channel="websocket", chat_id="1")
    assert len(sent) == 1
    assert sent[0].content == "real"


@pytest.mark.asyncio
async def test_message_tool_marks_channel_delivery_for_proactive_sends() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    await tool.execute(content="normal", channel="websocket", chat_id="1")
    await tool.execute(
        content="with file", channel="websocket", chat_id="1", media=["/tmp/generated.png"]
    )

    # Nessun contesto di turno ⇒ invii cross-target ⇒ proattivi, e un invio
    # proattivo va registrato in cronologia con o senza allegati: è ciò che il
    # modello ha detto all'utente da fuori la conversazione.
    expected = {"_record_channel_delivery": True, "_proactive_fanout": True}
    assert sent[0].metadata == expected
    assert sent[1].metadata == expected


@pytest.mark.asyncio
async def test_message_tool_does_not_record_a_plain_same_target_reply() -> None:
    """Una risposta nella chat corrente la persiste già il turno: registrarla qui
    la duplicherebbe. Il record scatta solo su allegato (per conservare i media)."""
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = _visible_tool(sent)

    await tool.execute(content="risposta")
    await tool.execute(content="ecco il file", media=["/tmp/generated.png"])

    assert "_record_channel_delivery" not in sent[0].metadata
    assert sent[1].metadata["_record_channel_delivery"] is True


@pytest.mark.asyncio
async def test_message_tool_records_media_deliveries() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    await tool.execute(
        content="image",
        channel="websocket",
        chat_id="chat-1",
        media=["/tmp/generated.png"],
    )

    assert sent[0].metadata == {"_record_channel_delivery": True, "_proactive_fanout": True}


@pytest.mark.asyncio
async def test_message_tool_inherits_metadata_for_same_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    thread_meta = {"thread": {"id": "111.222", "kind": "channel"}}
    from jenny.agent.tools.context import RequestContext

    tool.set_context(RequestContext(channel="websocket", chat_id="C123", metadata=thread_meta))

    await tool.execute(content="thread reply")

    assert sent[0].metadata == thread_meta


@pytest.mark.asyncio
async def test_message_tool_clears_metadata_when_context_has_none() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    from jenny.agent.tools.context import RequestContext

    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="C123",
            metadata={"thread": {"id": "111.222", "kind": "channel"}},
        ),
    )
    tool.set_context(RequestContext(channel="websocket", chat_id="C123", metadata={}))

    await tool.execute(content="plain reply")

    assert sent[0].metadata == {}


@pytest.mark.asyncio
async def test_message_tool_does_not_inherit_metadata_for_cross_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    from jenny.agent.tools.context import RequestContext

    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="C123",
            metadata={"thread": {"id": "111.222", "kind": "channel"}},
        ),
    )

    await tool.execute(content="channel reply", channel="other-channel", chat_id="C999")

    # Cross-target: nessun metadata ereditato dal turno, ma marcato proattivo
    # (fan-out) e da registrare in cronologia.
    assert sent[0].metadata == {
        "_record_channel_delivery": True,
        "_proactive_fanout": True,
    }


@pytest.mark.asyncio
async def test_message_tool_resolves_relative_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=["output/image.png"],
    )

    expected = str(get_workspace_path() / "output/image.png")
    assert sent[0].media == [expected]


@pytest.mark.asyncio
async def test_message_tool_resolves_relative_media_paths_from_active_workspace(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    tool = MessageTool(send_callback=_send, workspace=workspace)

    await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=["output/image.png"],
    )

    assert sent[0].media == [str(workspace / "output/image.png")]


@pytest.mark.asyncio
async def test_message_tool_rejects_outside_workspace_absolute_media_when_restricted(
    tmp_path,
) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = MessageTool(send_callback=_send, workspace=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=[str(outside)],
    )

    assert result.startswith("Error: media path is not allowed:")
    assert "outside allowed directory" in result
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_allows_workspace_absolute_media_when_restricted(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "image.png"
    image.write_text("image", encoding="utf-8")
    tool = MessageTool(send_callback=_send, workspace=workspace, restrict_to_workspace=True)

    result = await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=[str(image)],
    )

    assert result == "Message sent to websocket:1 with 1 attachments"
    assert sent[0].media == [str(image.resolve())]


@pytest.mark.asyncio
async def test_message_tool_passes_through_absolute_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    abs_path = os.path.abspath(os.path.join(os.sep, "tmp", "abs_image.png"))

    await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=[abs_path],
    )

    assert sent[0].media == [abs_path]


@pytest.mark.asyncio
async def test_message_tool_passes_through_url_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    url = "https://example.com/image.png"

    await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=[url],
    )

    assert sent[0].media == [url]


@pytest.mark.asyncio
async def test_message_tool_resolves_mixed_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)

    abs_path = os.path.abspath(os.path.join(os.sep, "tmp", "absolute.png"))

    await tool.execute(
        content="see attached",
        channel="websocket",
        chat_id="1",
        media=[
            "output/relative.png",
            abs_path,
            "https://example.com/url.png",
            "http://example.com/http.png",
        ],
    )

    expected_relative = str(get_workspace_path() / "output/relative.png")
    assert sent[0].media == [
        expected_relative,
        abs_path,
        "https://example.com/url.png",
        "http://example.com/http.png",
    ]


@pytest.mark.asyncio
async def test_message_tool_rejects_wrong_explicit_ws_chat_id(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    from jenny.agent.tools.context import RequestContext

    conv = "550e8400-e29b-41d4-a716-446655440000"
    tool.set_context(RequestContext(channel="websocket", chat_id=conv, metadata={}))
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    result = await tool.execute(
        content="see file",
        channel="websocket",
        chat_id="anon-deadbeefcafe",
        media=[str(f)],
    )
    assert result.startswith("Error: chat_id does not match")
    assert sent == []


@pytest.mark.asyncio
async def test_message_tool_allows_ws_explicit_when_matches_context(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    from jenny.agent.tools.context import RequestContext

    conv = "550e8400-e29b-41d4-a716-446655440000"
    tool.set_context(RequestContext(channel="websocket", chat_id=conv, metadata={}))
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    result = await tool.execute(
        content="see file",
        channel="websocket",
        chat_id=conv,
        media=[str(f)],
    )
    assert result.startswith("Message sent")
    assert sent[0].chat_id == conv


@pytest.mark.asyncio
async def test_message_tool_cli_context_may_target_other_ws_chat(tmp_path) -> None:
    """Cron / CLI handlers keep non-websocket defaults; explicit websocket + uuid remains valid."""
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    from jenny.agent.tools.context import RequestContext

    target = "550e8400-e29b-41d4-a716-446655440000"
    tool.set_context(RequestContext(channel="internal", chat_id="direct", metadata={}))
    f = tmp_path / "doc.md"
    f.write_text("hello", encoding="utf-8")
    result = await tool.execute(
        content="ping",
        channel="websocket",
        chat_id=target,
        media=[str(f)],
    )
    assert result.startswith("Message sent")
    assert sent[0].channel == "websocket"
    assert sent[0].chat_id == target


@pytest.mark.asyncio
async def test_no_proactive_fanout_for_current_conversation() -> None:
    """Un invio nella conversazione corrente (stesso canale+chat del turno)
    NON marca ``_proactive_fanout``: non deve diffondersi ad altri canali."""
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send, default_channel="websocket", default_chat_id="default"
    )
    await tool.execute(content="ecco il gatto", channel="websocket", chat_id="default")
    assert sent[0].metadata.get("_proactive_fanout") is None


@pytest.mark.asyncio
async def test_proactive_fanout_for_cross_channel_send() -> None:
    """Un invio cross-canale (diverso dal canale del turno) marca
    ``_proactive_fanout`` così il deliverer diffonde ai canali accoppiati."""
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send, default_channel="websocket", default_chat_id="default"
    )
    await tool.execute(content="promemoria", channel="telegram", chat_id="99")
    assert sent[0].metadata.get("_proactive_fanout") is True


# --- un solo avviso per ciclo silenzioso ---------------------------------------


def _silent_tool(sent: list[OutboundMessage]) -> MessageTool:
    from jenny.agent.tools.context import RequestContext
    from jenny.session.turn_visibility import silent_turn_metadata

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key="heartbeat",
            metadata=silent_turn_metadata(),
        )
    )
    tool.start_turn()
    return tool


def _visible_tool(sent: list[OutboundMessage]) -> MessageTool:
    from jenny.agent.tools.context import RequestContext

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send)
    tool.set_context(
        RequestContext(
            channel="websocket",
            chat_id="default",
            session_key="unified:default",
            metadata={},
        )
    )
    tool.start_turn()
    return tool


@pytest.mark.asyncio
async def test_a_silent_turn_delivers_its_first_alert() -> None:
    sent: list[OutboundMessage] = []
    tool = _silent_tool(sent)

    result = await tool.execute(content="umidità al 9%, sotto soglia")

    assert "Message sent" in result
    assert [m.content for m in sent] == ["umidità al 9%, sotto soglia"]


@pytest.mark.asyncio
async def test_a_silent_alert_is_marked_for_the_history() -> None:
    """L'avviso di un ciclo silenzioso gira su una sessione interna ma lo legge
    l'utente: senza questo marker il turno dopo non ne trova traccia (misurato
    il 2026-08-12: avviso WaterBot alle 18:33, "sicura?" alle 18:39 senza
    contesto)."""
    sent: list[OutboundMessage] = []
    tool = _silent_tool(sent)

    await tool.execute(content="hps non è raggiungibile")

    assert sent[0].metadata["_record_channel_delivery"] is True


@pytest.mark.asyncio
async def test_a_silent_turn_refuses_the_second_one() -> None:
    """Misurato sul dispositivo: un ciclo heartbeat ha consegnato cinque
    messaggi di fila ("sto aspettando", "ok basta, mi zitto", "🙄"). Un avviso
    è uno; il prompt lo vieta, questo lo rende impossibile."""
    sent: list[OutboundMessage] = []
    tool = _silent_tool(sent)

    await tool.execute(content="primo avviso")
    result = await tool.execute(content="🙄")

    assert result.startswith("Error: you already sent the one alert")
    assert [m.content for m in sent] == ["primo avviso"]


@pytest.mark.asyncio
async def test_the_refusal_tells_the_model_what_to_do_instead() -> None:
    sent: list[OutboundMessage] = []
    tool = _silent_tool(sent)
    await tool.execute(content="primo")

    result = await tool.execute(content="secondo")

    assert "at most one message per run" in result
    assert "Do not try again" in result


@pytest.mark.asyncio
async def test_the_next_run_gets_a_fresh_budget() -> None:
    """Il tetto è per turno, non per sessione: ``start_turn`` lo riazzera."""
    sent: list[OutboundMessage] = []
    tool = _silent_tool(sent)
    await tool.execute(content="ciclo 1")

    tool.start_turn()
    result = await tool.execute(content="ciclo 2")

    assert "Message sent" in result
    assert [m.content for m in sent] == ["ciclo 1", "ciclo 2"]


@pytest.mark.asyncio
async def test_a_visible_turn_is_not_capped() -> None:
    """Una conversazione vera può mandare più messaggi proattivi: il tetto è una
    proprietà del contratto silenzioso, non del tool."""
    sent: list[OutboundMessage] = []
    tool = _visible_tool(sent)

    await tool.execute(content="primo")
    result = await tool.execute(content="secondo")

    assert "Message sent" in result
    assert len(sent) == 2
