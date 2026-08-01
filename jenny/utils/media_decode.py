"""Shared helpers for decoding ``data:...;base64,...`` URLs to disk.

Used by the WebSocket channel for parsing uploaded media with consistent
size guards and filesystem layout.
"""

from __future__ import annotations

import base64
import mimetypes
import re
import uuid
from pathlib import Path

from jenny.utils.helpers import safe_filename

DEFAULT_MAX_BYTES = 10 * 1024 * 1024

_DATA_URL_RE = re.compile(r"^data:([^;,]+)(?:;[^,]*)*;base64,(.+)$", re.DOTALL)
_MIME_EXTENSION_OVERRIDES = {
    # Prefer the canonical container extension over platform-dependent
    # ``mimetypes`` guesses.
    "video/webm": ".webm",
}


class FileSizeExceeded(Exception):  # noqa: N818 (nome storico, usato ovunque senza suffisso Error)
    """Raised when a decoded payload exceeds the caller's size limit."""


_GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})


def _resolve_extension(mime_type: str, original_name: str | None) -> str:
    """Ricava l'estensione del file salvato.

    Preferisce l'estensione canonica dal MIME; per i MIME generici
    (``application/octet-stream``) o sconosciuti ricade sull'estensione del
    nome originale (es. un ``.docx`` inviato come octet-stream mantiene
    ``.docx`` invece di diventare ``.bin``).
    """
    override = _MIME_EXTENSION_OVERRIDES.get(mime_type)
    if override:
        return override
    if mime_type not in _GENERIC_MIME_TYPES:
        guessed = mimetypes.guess_extension(mime_type)
        if guessed:
            return guessed
    if original_name:
        suffix = Path(original_name).suffix
        if suffix and len(suffix) <= 16:
            return suffix.lower()
    return ".bin"


def save_base64_data_url(
    data_url: str,
    media_dir: Path,
    *,
    max_bytes: int | None = None,
    original_name: str | None = None,
) -> str | None:
    """Decode a ``data:<mime>;base64,<payload>`` URL and persist it.

    Returns the absolute path on success, ``None`` when the URL shape or the
    base64 payload itself is malformed. Raises :class:`FileSizeExceeded`
    when the decoded payload is larger than ``max_bytes`` (default 10 MB).

    ``original_name`` (opzionale) viene usato per due cose: dedurre
    l'estensione quando il MIME è generico/sconosciuto e rendere leggibile il
    nome salvato (prefisso uuid anti-collisione + nome originale sanitizzato).
    """
    m = _DATA_URL_RE.match(data_url)
    if not m:
        return None
    mime_type, b64_payload = m.group(1).strip().lower(), m.group(2)
    try:
        raw = base64.b64decode(b64_payload)
    except Exception:
        return None
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise FileSizeExceeded(f"File exceeds {limit // (1024 * 1024)}MB limit")
    ext = _resolve_extension(mime_type, original_name)
    prefix = uuid.uuid4().hex[:12]
    if original_name:
        stem = safe_filename(Path(original_name).stem)[:64].strip("._-")
        filename = f"{prefix}-{stem}{ext}" if stem else f"{prefix}{ext}"
    else:
        filename = f"{prefix}{ext}"
    dest = media_dir / safe_filename(filename)
    dest.write_bytes(raw)
    return str(dest)
