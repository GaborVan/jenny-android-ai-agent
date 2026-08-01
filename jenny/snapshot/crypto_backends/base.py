"""Contratto comune dei backend di cifratura."""

from __future__ import annotations

from typing import Protocol


class CryptoAuthError(Exception):
    """Autenticazione fallita: passphrase errata, dato corrotto o manomesso."""


class CryptoUnavailableError(Exception):
    """Nessun backend di cifratura utilizzabile in questo ambiente."""


class CryptoBackend(Protocol):
    """AEAD AES-256-GCM: cifra/decifra un payload monolitico con AAD."""

    async def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
        """Ritorna ciphertext+tag. ``key`` 32 byte, ``nonce`` 12 byte."""
        ...

    async def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
        """Ritorna il plaintext.

        Raises:
            CryptoAuthError: tag non valido (chiave errata o dato manomesso).
        """
        ...
