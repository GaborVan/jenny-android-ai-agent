"""Formato del container di backup cifrato (file ``.jbk``).

Layout binario (header di 37 byte, legato come AAD alla cifratura, così ogni
manomissione dell'header invalida il tag):

    magic ``JNBK`` (4) | version (1) | kdf_iterations uint32 BE (4)
    | salt (16) | nonce (12) | ciphertext+tag AES-256-GCM (resto)

Chiave = PBKDF2-HMAC-SHA256(passphrase, salt, iterations, 32 byte) — tutto
stdlib, identico su Android e desktop.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import struct

from jenny.snapshot.crypto_backends.base import CryptoAuthError, CryptoBackend

MAGIC = b"JNBK"
FORMAT_VERSION = 1
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
HEADER_LEN = len(MAGIC) + 1 + 4 + SALT_LEN + NONCE_LEN  # 37
DEFAULT_KDF_ITERATIONS = 600_000
# Tetto sulle iterazioni dichiarate nell'header: l'AAD rivela la manomissione
# solo DOPO la KDF, quindi senza tetto un file corrotto/ostile con
# iterations=2^32-1 terrebbe la CPU occupata per ore prima di fallire.
MAX_KDF_ITERATIONS = 10_000_000
BACKUP_FILE_EXTENSION = ".jbk"


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """Deriva la chiave AES dalla passphrase (PBKDF2-HMAC-SHA256, stdlib)."""
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=KEY_LEN
    )


def build_header(iterations: int, salt: bytes, nonce: bytes) -> bytes:
    if len(salt) != SALT_LEN or len(nonce) != NONCE_LEN:
        raise ValueError("invalid salt/nonce length")
    return MAGIC + bytes([FORMAT_VERSION]) + struct.pack(">I", iterations) + salt + nonce


def parse_header(data: bytes) -> tuple[int, bytes, bytes]:
    """Valida e spacchetta l'header; ritorna ``(iterations, salt, nonce)``.

    Raises:
        CryptoAuthError: file troppo corto, magic errato o versione ignota.
    """
    if len(data) <= HEADER_LEN:
        raise CryptoAuthError("not a Jenny backup file (truncated)")
    if data[: len(MAGIC)] != MAGIC:
        raise CryptoAuthError("not a Jenny backup file (bad magic)")
    version = data[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise CryptoAuthError(f"unsupported backup format version {version}")
    offset = len(MAGIC) + 1
    (iterations,) = struct.unpack(">I", data[offset : offset + 4])
    if not 1 <= iterations <= MAX_KDF_ITERATIONS:
        raise CryptoAuthError(f"implausible KDF iteration count {iterations} (corrupt header)")
    offset += 4
    salt = data[offset : offset + SALT_LEN]
    offset += SALT_LEN
    nonce = data[offset : offset + NONCE_LEN]
    return iterations, salt, nonce


def get_crypto_backend() -> CryptoBackend:
    """Backend AES-GCM per l'ambiente corrente (javax su Android, dev altrove)."""
    from jenny.runtime.context import get_android_context

    if get_android_context() is not None:
        from jenny.snapshot.crypto_backends.android import AndroidAesGcmBackend

        return AndroidAesGcmBackend()
    from jenny.snapshot.crypto_backends.dev import DevAesGcmBackend

    return DevAesGcmBackend()


async def encrypt_container(
    passphrase: str,
    plaintext: bytes,
    *,
    iterations: int = DEFAULT_KDF_ITERATIONS,
    backend: CryptoBackend | None = None,
    _salt: bytes | None = None,
    _nonce: bytes | None = None,
) -> bytes:
    """Cifra ``plaintext`` in un container ``.jbk`` completo.

    ``_salt``/``_nonce`` esistono SOLO per i test (known-answer test vector);
    in produzione sono sempre generati con ``secrets``.
    """
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    if not 1 <= iterations <= MAX_KDF_ITERATIONS:
        raise ValueError(f"kdf iterations out of range: {iterations}")
    backend = backend or get_crypto_backend()
    salt = _salt if _salt is not None else secrets.token_bytes(SALT_LEN)
    nonce = _nonce if _nonce is not None else secrets.token_bytes(NONCE_LEN)
    header = build_header(iterations, salt, nonce)
    # PBKDF2 con centinaia di migliaia di iterazioni è CPU-bound: off-thread.
    key = await asyncio.to_thread(derive_key, passphrase, salt, iterations)
    ciphertext = await backend.encrypt(key, nonce, plaintext, header)
    return header + ciphertext


async def decrypt_container(
    passphrase: str,
    data: bytes,
    *,
    backend: CryptoBackend | None = None,
) -> bytes:
    """Decifra un container ``.jbk``.

    Raises:
        CryptoAuthError: formato non riconosciuto, passphrase errata o dato
            corrotto/manomesso (header incluso, essendo legato come AAD).
    """
    backend = backend or get_crypto_backend()
    iterations, salt, nonce = parse_header(data)
    header = data[:HEADER_LEN]
    ciphertext = data[HEADER_LEN:]
    key = await asyncio.to_thread(derive_key, passphrase, salt, iterations)
    return await backend.decrypt(key, nonce, ciphertext, header)
