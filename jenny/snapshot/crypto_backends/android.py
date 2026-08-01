"""Backend AES-GCM su ``javax.crypto`` (Android, via Chaquopy).

Zero dipendenze Python: usa la JCA della piattaforma direttamente da Python
con ``jclass``. Non serve nemmeno l'Android Context — è pura crittografia
JCA — ma il backend viene selezionato solo quando il runtime è Android
(vedi ``crypto.get_crypto_backend``). Le operazioni girano off-thread:
``doFinal`` su decine di MB è CPU-bound.

Il formato prodotto è AES-256-GCM standard, byte-identico a quello del
backend dev (``cryptography``): i known-answer test vector in
``tests/snapshot/test_crypto_roundtrip.py`` valgono per entrambi.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jenny.snapshot.crypto_backends.base import CryptoAuthError

_GCM_TAG_BITS = 128

_classes: dict[str, Any] | None = None


def _jca() -> dict[str, Any]:
    """Risolve (una volta) le classi JCA via Chaquopy."""
    global _classes
    if _classes is None:
        from java import jclass  # importabile solo sotto Chaquopy

        _classes = {
            "Cipher": jclass("javax.crypto.Cipher"),
            "SecretKeySpec": jclass("javax.crypto.spec.SecretKeySpec"),
            "GCMParameterSpec": jclass("javax.crypto.spec.GCMParameterSpec"),
        }
    return _classes


def _run_cipher(mode_encrypt: bool, key: bytes, nonce: bytes, data: bytes, aad: bytes) -> bytes:
    jca = _jca()
    cipher = jca["Cipher"].getInstance("AES/GCM/NoPadding")
    key_spec = jca["SecretKeySpec"](key, "AES")
    gcm_spec = jca["GCMParameterSpec"](_GCM_TAG_BITS, nonce)
    mode = jca["Cipher"].ENCRYPT_MODE if mode_encrypt else jca["Cipher"].DECRYPT_MODE
    cipher.init(mode, key_spec, gcm_spec)
    cipher.updateAAD(aad)
    try:
        return bytes(cipher.doFinal(data))
    except Exception as exc:  # noqa: BLE001 - le eccezioni Java arrivano wrappate
        # AEADBadTagException (tag non valido) → errore di autenticazione.
        # Il match sul nome evita di dover importare la classe Java per il catch.
        name = type(exc).__name__
        if "AEADBadTag" in name or "BadPadding" in name or "AEADBadTag" in str(exc):
            raise CryptoAuthError(
                "authentication failed: wrong passphrase or corrupt data"
            ) from exc
        raise


class AndroidAesGcmBackend:
    """Implementazione di :class:`CryptoBackend` su javax.crypto."""

    async def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
        return await asyncio.to_thread(_run_cipher, True, key, nonce, plaintext, aad)

    async def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        return await asyncio.to_thread(_run_cipher, False, key, nonce, ciphertext, aad)
