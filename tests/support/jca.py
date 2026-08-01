"""Fake di javax.crypto (JCA) per testare ``AndroidAesGcmBackend`` su host.

Riproduce il sottoinsieme usato da ``jenny/snapshot/crypto_backends/android.py``:
``Cipher`` AES/GCM/NoPadding con AAD, ``SecretKeySpec``, ``GCMParameterSpec``.
La crittografia vera è delegata a pyca/``cryptography``, così la parità di
byte col backend dev è verificata davvero (stessi vettori known-answer) e non
solo simulata.
"""

from __future__ import annotations

from typing import Any

import pytest


# I nomi ricalcano le classi Java: il backend fa match sul nome dell'eccezione.
class AEADBadTagException(Exception):  # noqa: N818
    """Nome allineato a ``javax.crypto.AEADBadTagException`` (match sul nome)."""


class BadPaddingException(Exception):  # noqa: N818
    """Nome allineato a ``javax.crypto.BadPaddingException`` (match sul nome)."""


class FakeSecretKeySpec:
    def __init__(self, key: bytes, algorithm: str) -> None:
        assert algorithm == "AES"
        self.key = bytes(key)


class FakeGCMParameterSpec:
    def __init__(self, tag_bits: int, nonce: bytes) -> None:
        self.tag_bits = tag_bits
        self.nonce = bytes(nonce)


class FakeCipher:
    ENCRYPT_MODE = 1
    DECRYPT_MODE = 2

    #: Eccezione da sollevare in ``doFinal`` al posto dell'operazione (per i
    #: test sul mapping delle eccezioni Java); condivisa da tutte le istanze,
    #: resettata da ``install_fake_jca``.
    fail_with: Exception | None = None

    def __init__(self) -> None:
        self._mode: int | None = None
        self._key: bytes = b""
        self._spec: FakeGCMParameterSpec | None = None
        self._aad = b""

    @classmethod
    def getInstance(cls, transformation: str) -> "FakeCipher":  # noqa: N802
        assert transformation == "AES/GCM/NoPadding"
        return cls()

    def init(self, mode: int, key_spec: FakeSecretKeySpec, spec: FakeGCMParameterSpec) -> None:
        assert spec.tag_bits == 128
        self._mode = mode
        self._key = key_spec.key
        self._spec = spec

    def updateAAD(self, aad: bytes) -> None:  # noqa: N802
        self._aad += bytes(aad)

    def doFinal(self, data: bytes) -> bytes:  # noqa: N802
        if FakeCipher.fail_with is not None:
            raise FakeCipher.fail_with
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        assert self._spec is not None
        aead = AESGCM(self._key)
        if self._mode == self.ENCRYPT_MODE:
            return aead.encrypt(self._spec.nonce, bytes(data), self._aad)
        try:
            return aead.decrypt(self._spec.nonce, bytes(data), self._aad)
        except InvalidTag as exc:
            raise AEADBadTagException(str(exc)) from exc


JCA_CLASSES: dict[str, Any] = {
    "javax.crypto.Cipher": FakeCipher,
    "javax.crypto.spec.SecretKeySpec": FakeSecretKeySpec,
    "javax.crypto.spec.GCMParameterSpec": FakeGCMParameterSpec,
}


def install_fake_jca(monkeypatch: pytest.MonkeyPatch) -> type[FakeCipher]:
    """Monta il modulo ``java`` finto e resetta la cache classi del backend."""
    import jenny.snapshot.crypto_backends.android as android_mod
    from support.android import fake_java_module

    fake_java_module(monkeypatch, JCA_CLASSES)
    # La cache _classes è globale nel modulo: va azzerata tra un test e l'altro.
    monkeypatch.setattr(android_mod, "_classes", None)
    monkeypatch.setattr(FakeCipher, "fail_with", None)
    return FakeCipher
