"""Test per il filtro anti-rumore degli handshake WebSocket."""

from __future__ import annotations

import logging

from websockets.exceptions import InvalidMessage

from jenny.channels.ws_logging import (
    OPENING_HANDSHAKE_FAILED_MESSAGE,
    WebSocketHandshakeNoiseFilter,
)


def _handshake_record(exc: BaseException | None) -> logging.LogRecord:
    record = logging.LogRecord(
        name="websockets.server",
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg=OPENING_HANDSHAKE_FAILED_MESSAGE,
        args=(),
        exc_info=(type(exc), exc, None) if exc else None,
    )
    return record


def test_suppresses_eof_probe_disconnect() -> None:
    # Probe TCP che chiude prima della request line: InvalidMessage <- EOFError.
    cause = EOFError("connection closed while reading HTTP request line")
    exc = InvalidMessage("did not receive a valid HTTP request")
    exc.__cause__ = cause

    filt = WebSocketHandshakeNoiseFilter()
    assert filt.filter(_handshake_record(exc)) is False


def test_keeps_real_handshake_failure() -> None:
    # Un handshake fallito per un motivo reale (non una disconnessione) resta.
    exc = InvalidMessage("did not receive a valid HTTP request")
    exc.__cause__ = ValueError("malformed header")

    filt = WebSocketHandshakeNoiseFilter()
    assert filt.filter(_handshake_record(exc)) is True


def test_keeps_unrelated_records() -> None:
    filt = WebSocketHandshakeNoiseFilter()
    record = logging.LogRecord(
        name="websockets.server",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="connection open",
        args=(),
        exc_info=None,
    )
    assert filt.filter(record) is True
