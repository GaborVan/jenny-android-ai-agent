"""Test di ``AndroidAesGcmBackend`` con una JCA finta (vedi ``support/jca.py``).

Il modulo ``java`` di Chaquopy non esiste su host: la JCA finta delega la
crittografia vera a pyca/``cryptography``, così questi test verificano sul
serio la parità di byte col backend dev (stessi known-answer vector di
``test_crypto_roundtrip.py``) e il mapping delle eccezioni Java.
"""

from __future__ import annotations

import pytest

from jenny.snapshot.crypto import build_header, decrypt_container, encrypt_container
from jenny.snapshot.crypto_backends.android import AndroidAesGcmBackend
from jenny.snapshot.crypto_backends.base import CryptoAuthError

pytest.importorskip("cryptography")

from support.jca import BadPaddingException, install_fake_jca  # noqa: E402

from snapshot.test_crypto_roundtrip import (  # noqa: E402
    KAT_CONTAINER,
    KAT_ITERATIONS,
    KAT_NONCE,
    KAT_PASSPHRASE,
    KAT_PLAINTEXT,
    KAT_RAW_CIPHERTEXT,
    KAT_RAW_KEY,
    KAT_SALT,
)


@pytest.fixture()
def fake_jca(monkeypatch: pytest.MonkeyPatch):
    return install_fake_jca(monkeypatch)


async def test_encrypt_known_answer_matches_dev_backend(fake_jca) -> None:
    """Stessi input → stessi byte del backend dev (parità cross-backend)."""
    aad = build_header(KAT_ITERATIONS, KAT_SALT, KAT_NONCE)
    out = await AndroidAesGcmBackend().encrypt(KAT_RAW_KEY, KAT_NONCE, KAT_PLAINTEXT, aad)
    assert out == KAT_RAW_CIPHERTEXT


async def test_container_known_answer(fake_jca) -> None:
    blob = await encrypt_container(
        KAT_PASSPHRASE,
        KAT_PLAINTEXT,
        iterations=KAT_ITERATIONS,
        backend=AndroidAesGcmBackend(),
        _salt=KAT_SALT,
        _nonce=KAT_NONCE,
    )
    assert blob == KAT_CONTAINER


async def test_cross_backend_roundtrip(fake_jca) -> None:
    """Un container cifrato da un backend è decifrabile dall'altro."""
    from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend

    android, dev = AndroidAesGcmBackend(), DevAesGcmBackend()
    blob = await encrypt_container("pass àè", b"payload" * 100, iterations=1000, backend=android)
    assert (await decrypt_container("pass àè", blob, backend=dev)) == b"payload" * 100

    blob = await encrypt_container("pass àè", b"altro", iterations=1000, backend=dev)
    assert (await decrypt_container("pass àè", blob, backend=android)) == b"altro"


async def test_wrong_passphrase_maps_aead_bad_tag_to_auth_error(fake_jca) -> None:
    """Il tag GCM invalido (AEADBadTagException) diventa CryptoAuthError."""
    backend = AndroidAesGcmBackend()
    blob = await encrypt_container("giusta", b"dati", iterations=1000, backend=backend)
    with pytest.raises(CryptoAuthError):
        await decrypt_container("sbagliata", blob, backend=backend)


async def test_bad_padding_exception_maps_to_auth_error(fake_jca) -> None:
    fake_jca.fail_with = BadPaddingException("bad padding")
    with pytest.raises(CryptoAuthError):
        await AndroidAesGcmBackend().decrypt(b"\x00" * 32, b"\x00" * 12, b"x" * 16, b"")


async def test_unrelated_java_exception_is_reraised(fake_jca) -> None:
    """Solo gli errori di autenticazione diventano CryptoAuthError; il resto risale."""

    class IllegalStateException(Exception):  # noqa: N818 - nome Java reale
        pass

    fake_jca.fail_with = IllegalStateException("cipher not initialized")
    with pytest.raises(IllegalStateException):
        await AndroidAesGcmBackend().encrypt(b"\x00" * 32, b"\x00" * 12, b"x", b"")
