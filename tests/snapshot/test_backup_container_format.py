"""Test del formato container .jbk: magic, versione, troncamenti."""

from __future__ import annotations

import pytest

from jenny.snapshot.crypto import (
    FORMAT_VERSION,
    HEADER_LEN,
    MAGIC,
    build_header,
    parse_header,
)
from jenny.snapshot.crypto_backends.base import CryptoAuthError

SALT = bytes(16)
NONCE = bytes(12)


def test_header_layout() -> None:
    header = build_header(600_000, SALT, NONCE)
    assert len(header) == HEADER_LEN
    assert header.startswith(MAGIC)
    assert header[len(MAGIC)] == FORMAT_VERSION


def test_bad_magic_rejected() -> None:
    data = b"ZIP!" + build_header(1000, SALT, NONCE)[4:] + b"x" * 32
    with pytest.raises(CryptoAuthError, match="magic"):
        parse_header(data)


def test_unknown_version_rejected() -> None:
    header = bytearray(build_header(1000, SALT, NONCE))
    header[len(MAGIC)] = 99
    with pytest.raises(CryptoAuthError, match="version"):
        parse_header(bytes(header) + b"x" * 32)


def test_truncated_rejected() -> None:
    header = build_header(1000, SALT, NONCE)
    with pytest.raises(CryptoAuthError, match="truncated"):
        parse_header(header)  # header senza ciphertext
    with pytest.raises(CryptoAuthError, match="truncated"):
        parse_header(header[:10])
    with pytest.raises(CryptoAuthError, match="truncated"):
        parse_header(b"")


def test_invalid_salt_nonce_length() -> None:
    with pytest.raises(ValueError):
        build_header(1000, b"corto", NONCE)
    with pytest.raises(ValueError):
        build_header(1000, SALT, b"corto")
