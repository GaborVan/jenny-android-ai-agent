"""Test di cifratura del backup: roundtrip, autenticazione, known-answer.

I test vector hardcoded qui sono il riferimento anche per verificare
l'implementazione javax.crypto on-device: stessi input → stessi byte.
"""

from __future__ import annotations

import pytest

from jenny.snapshot.crypto import (
    DEFAULT_KDF_ITERATIONS,
    HEADER_LEN,
    decrypt_container,
    derive_key,
    encrypt_container,
    parse_header,
)
from jenny.snapshot.crypto_backends.base import CryptoAuthError

pytest.importorskip("cryptography")

from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend  # noqa: E402

# --- Known-answer test vectors (riferimento cross-implementazione) ---------
KAT_PASSPHRASE = "passphrase-di-prova"
KAT_SALT = bytes(range(16))
KAT_NONCE = bytes(range(12))
KAT_ITERATIONS = 1000
KAT_PLAINTEXT = b"contenuto segreto del backup"
# PBKDF2-HMAC-SHA256(passphrase, salt, 1000, 32)
KAT_DERIVED_KEY = bytes.fromhex(
    "e5d3093506cfbed1a194759109f7eb5de30fe9e3671ce8894fc7e91b716bc99a"
)
# AES-256-GCM(key=00..1f, nonce=00..0b, aad=header(1000, salt, nonce))
KAT_RAW_KEY = bytes(range(32))
KAT_RAW_CIPHERTEXT = bytes.fromhex(
    "246db86fa08bb76fe261e4eed69b1d19ecf6e3519c5b3d1d5b0c90f569feb75c22dd36e17ff51fb2f7a24cc0"
)
# Container completo: encrypt_container(KAT_PASSPHRASE, KAT_PLAINTEXT, 1000, salt, nonce)
KAT_CONTAINER = bytes.fromhex(
    "4a4e424b01000003e8000102030405060708090a0b0c0d0e0f000102030405060708090a0b"
    "d7f2633b425e70fd8d7107b892f69736c5b62d0b792c4cd7fe2e8282ffdc51f7"
    "7370f666d08b1849fb71c18a"
)


def test_derive_key_known_answer() -> None:
    assert derive_key(KAT_PASSPHRASE, KAT_SALT, KAT_ITERATIONS) == KAT_DERIVED_KEY


async def test_backend_encrypt_known_answer() -> None:
    from jenny.snapshot.crypto import build_header

    aad = build_header(KAT_ITERATIONS, KAT_SALT, KAT_NONCE)
    out = await DevAesGcmBackend().encrypt(KAT_RAW_KEY, KAT_NONCE, KAT_PLAINTEXT, aad)
    assert out == KAT_RAW_CIPHERTEXT


async def test_container_known_answer() -> None:
    blob = await encrypt_container(
        KAT_PASSPHRASE,
        KAT_PLAINTEXT,
        iterations=KAT_ITERATIONS,
        backend=DevAesGcmBackend(),
        _salt=KAT_SALT,
        _nonce=KAT_NONCE,
    )
    assert blob == KAT_CONTAINER


async def test_roundtrip() -> None:
    backend = DevAesGcmBackend()
    blob = await encrypt_container(
        "un'altra passphrase àè", b"payload" * 1000, iterations=1000, backend=backend
    )
    assert (await decrypt_container("un'altra passphrase àè", blob, backend=backend)) == (
        b"payload" * 1000
    )


async def test_wrong_passphrase_rejected() -> None:
    backend = DevAesGcmBackend()
    blob = await encrypt_container("giusta", b"dati", iterations=1000, backend=backend)
    with pytest.raises(CryptoAuthError):
        await decrypt_container("sbagliata", blob, backend=backend)


async def test_tampered_ciphertext_rejected() -> None:
    backend = DevAesGcmBackend()
    blob = bytearray(
        await encrypt_container("pass", b"dati importanti", iterations=1000, backend=backend)
    )
    blob[-1] ^= 0xFF
    with pytest.raises(CryptoAuthError):
        await decrypt_container("pass", bytes(blob), backend=backend)


async def test_tampered_header_rejected() -> None:
    """L'header è legato come AAD: manometterlo invalida il tag."""
    backend = DevAesGcmBackend()
    blob = bytearray(
        await encrypt_container("pass", b"dati", iterations=1000, backend=backend)
    )
    blob[HEADER_LEN - 1] ^= 0x01  # ultimo byte del nonce
    with pytest.raises(CryptoAuthError):
        await decrypt_container("pass", bytes(blob), backend=backend)


async def test_empty_passphrase_rejected() -> None:
    with pytest.raises(ValueError):
        await encrypt_container("", b"dati", backend=DevAesGcmBackend())


def test_default_iterations_reasonable() -> None:
    assert DEFAULT_KDF_ITERATIONS >= 100_000


def test_parse_header_roundtrip() -> None:
    iterations, salt, nonce = parse_header(KAT_CONTAINER)
    assert iterations == KAT_ITERATIONS
    assert salt == KAT_SALT
    assert nonce == KAT_NONCE
