"""Backend AES-GCM per desktop/test basato su ``cryptography``.

``cryptography`` NON è una dipendenza runtime: ha binding nativi che
violerebbero la regola pure-Python dei requirements Android. È installata
solo nel venv di sviluppo e nel job di test in CI; l'import è lazy così
questo modulo si può importare ovunque senza errori.
"""

from __future__ import annotations

import asyncio

from jenny.snapshot.crypto_backends.base import CryptoAuthError, CryptoUnavailableError


def _load_aesgcm():
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dipende dall'ambiente
        raise CryptoUnavailableError(
            "the 'cryptography' package is required for backup encryption "
            "outside Android (pip install cryptography)"
        ) from exc
    return AESGCM, InvalidTag


class DevAesGcmBackend:
    """Implementazione di :class:`CryptoBackend` su pyca/cryptography."""

    async def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
        aesgcm_cls, _ = _load_aesgcm()
        return await asyncio.to_thread(aesgcm_cls(key).encrypt, nonce, plaintext, aad)

    async def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        aesgcm_cls, invalid_tag = _load_aesgcm()
        try:
            return await asyncio.to_thread(aesgcm_cls(key).decrypt, nonce, ciphertext, aad)
        except invalid_tag as exc:
            raise CryptoAuthError("authentication failed: wrong passphrase or corrupt data") from exc
