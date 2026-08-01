from unittest.mock import MagicMock

import pytest

from jenny.bus.events import OutboundMessage
from jenny.bus.queue import MessageBus
from jenny.channels.websocket import WebSocketChannel


def _mock_gateway() -> MagicMock:
    g = MagicMock()
    g.http = MagicMock()
    g.tokens = MagicMock()
    g.media = MagicMock()
    g.transcripts = MagicMock()
    g.workspaces = MagicMock()
    return g


class _DummyChannel(WebSocketChannel):
    name = "dummy"
    _sent: list[OutboundMessage]

    def __init__(self, config, bus):
        super().__init__(config, bus, gateway=_mock_gateway())
        self._sent = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(self, msg: OutboundMessage) -> None:
        self._sent.append(msg)


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel({"allowFrom": ["allow@email.com"]}, MessageBus())

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


def test_is_allowed_supports_dict_allow_from_alias() -> None:
    channel = _DummyChannel({"allowFrom": ["alice"]}, MessageBus())

    assert channel.is_allowed("alice") is True


def test_is_allowed_denies_empty_dict_allow_from() -> None:
    channel = _DummyChannel({"allow_from": []}, MessageBus())

    assert channel.is_allowed("alice") is False


def test_is_allowed_star_allows_all() -> None:
    channel = _DummyChannel({"allowFrom": ["*"]}, MessageBus())
    assert channel.is_allowed("anyone") is True


@pytest.mark.asyncio
async def test_handle_message_ignores_unknown() -> None:
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": []}, bus)

    await channel._handle_message(
        sender_id="stranger", chat_id="chat1", content="hello"
    )

    assert channel._sent == []

