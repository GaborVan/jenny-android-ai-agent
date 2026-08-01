"""Test della selezione del backend crypto in base al runtime.

``get_crypto_backend`` è il punto di branch Android/host: con un Android
Context registrato deve scegliere javax.crypto, altrimenti il backend dev.
"""

from __future__ import annotations

import sys

import pytest
from support.android import force_android_context, force_no_android_context

from jenny.snapshot.crypto import get_crypto_backend
from jenny.snapshot.crypto_backends.android import AndroidAesGcmBackend
from jenny.snapshot.crypto_backends.base import CryptoUnavailableError
from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend


def test_android_context_selects_javax_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    force_android_context(monkeypatch)
    assert isinstance(get_crypto_backend(), AndroidAesGcmBackend)


def test_host_selects_dev_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    force_no_android_context(monkeypatch)
    assert isinstance(get_crypto_backend(), DevAesGcmBackend)


async def test_dev_backend_without_cryptography_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Senza ``cryptography`` installata il backend dev fallisce in modo tipizzato.

    ``sys.modules[...] = None`` fa fallire l'import anche quando il pacchetto
    è presente nel venv: simula l'ambiente senza dipendenza.
    """
    monkeypatch.setitem(sys.modules, "cryptography.exceptions", None)
    with pytest.raises(CryptoUnavailableError):
        await DevAesGcmBackend().encrypt(b"\x00" * 32, b"\x00" * 12, b"dati", b"")
    with pytest.raises(CryptoUnavailableError):
        await DevAesGcmBackend().decrypt(b"\x00" * 32, b"\x00" * 12, b"dati", b"")
