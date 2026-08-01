"""Blob store content-addressed: sha256 come chiave, zlib come compressione.

Il nome del blob È il suo hash: ogni corruzione su disco è auto-rilevabile
alla lettura. La deduplica è implicita (stesso contenuto → stesso path).
"""

from __future__ import annotations

import hashlib
import os
import uuid
import zlib
from pathlib import Path


class BlobCorruptError(Exception):
    """Il contenuto di un blob non corrisponde al suo hash dichiarato."""


def hash_content(content: bytes) -> str:
    """Sha256 esadecimale del contenuto raw (non compresso)."""
    return hashlib.sha256(content).hexdigest()


def object_path(objects_dir: Path, hash_hex: str) -> Path:
    """Path del blob per un dato hash (sharding a 2 caratteri, come git)."""
    return objects_dir / hash_hex[:2] / hash_hex


def put_blob(objects_dir: Path, content: bytes) -> str:
    """Scrive il blob se assente e ritorna il suo hash.

    La scrittura passa da un file temporaneo + ``os.replace`` così un crash a
    metà non lascia mai un blob parziale con il nome definitivo.
    """
    hash_hex = hash_content(content)
    dest = object_path(objects_dir, hash_hex)
    if dest.is_file():
        return hash_hex

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Nome tmp unico per chiamata: due scrittori concorrenti dello stesso blob
    # non condividono mai il temporaneo, altrimenti l'``os.replace`` del primo
    # porta via l'inode mentre il secondo ci sta ancora scrivendo (blob troncato
    # o FileNotFoundError). Stessa scelta di ``atomic_write``.
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(zlib.compress(content))
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return hash_hex


def get_blob(objects_dir: Path, hash_hex: str) -> bytes:
    """Legge e decomprime un blob, verificandone l'integrità.

    Raises:
        FileNotFoundError: blob assente.
        BlobCorruptError: contenuto che non corrisponde all'hash.
    """
    raw = object_path(objects_dir, hash_hex).read_bytes()
    try:
        content = zlib.decompress(raw)
    except zlib.error as exc:
        raise BlobCorruptError(f"blob {hash_hex} is not valid zlib data") from exc
    if hash_content(content) != hash_hex:
        raise BlobCorruptError(f"blob {hash_hex} content does not match its hash")
    return content


def iter_blob_hashes(objects_dir: Path):
    """Itera gli hash di tutti i blob presenti nello store."""
    if not objects_dir.is_dir():
        return
    for shard in objects_dir.iterdir():
        if not shard.is_dir():
            continue
        for blob in shard.iterdir():
            if blob.is_file() and not blob.name.endswith(".tmp"):
                yield blob.name
