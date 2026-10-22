"""Logica pura della sync memoria↔Google Drive: nessun I/O qui dentro.

Tutte le decisioni (chi vince, cosa si scarica, cosa si cancella) vivono in
``plan_sync``, una funzione deterministica su tre snapshot in memoria. Gli
adapter (``drive_sync_local.py`` per il filesystem, ``drive_sync_bridge.py``
per Android) restano thin e fanno solo I/O; ``drive_sync.py`` li orchestra.
Separare così rende l'algoritmo testabile con dizionari sintetici, senza un
device o un filesystem finto.
"""

from __future__ import annotations

from dataclasses import dataclass

# Nomi alla radice del workspace che partecipano alla sync (v. AGENTS.md /
# docs/using/memory.md). Tutto il resto di ``memory/`` (ricorsivo) partecipa
# per prefisso.
ROOT_SCOPE_FILES = ("SOUL.md", "USER.md")
MEMORY_PREFIX = "memory/"
_ENCODED_MEMORY_PREFIX = "memory__"

# Scope condiviso: mirror locale ``shared/`` della cartella Drive condivisa
# ("Apex-Pamyat") riservata a profile/knowledge/notes — v. docs/using/
# shared-memory.md. Solo le sottocartelle elencate partecipano; qualunque
# altra voce (file sparsi nella root della cartella, sottocartelle estranee,
# il manifest stesso) viene ignorata, mai toccata.
SHARED_PREFIX = "shared/"
_ENCODED_SHARED_PREFIX = "shared__"
SHARED_SUBFOLDERS = ("profile", "knowledge", "notes")


@dataclass(frozen=True)
class FileMeta:
    """Metadati di un file sincronizzato: mtime in epoch seconds, sha256 hex."""

    mtime: float
    sha256: str


@dataclass(frozen=True)
class SyncPlan:
    """Esito di ``plan_sync``: nomi codificati raggruppati per azione."""

    uploads: tuple[str, ...]
    downloads: tuple[str, ...]
    deletes_remote: tuple[str, ...]
    skipped: tuple[str, ...]


def encode_name(relpath: str) -> str:
    """``SOUL.md`` invariato; ``memory/x/y.md`` -> ``memory__x__y.md``;
    ``shared/profile/USER.md`` -> ``shared__profile__USER.md`` (stessa
    disciplina di appiattimento di ``memory/``).
    """
    if relpath in ROOT_SCOPE_FILES:
        return relpath
    if relpath.startswith(SHARED_PREFIX):
        rest = relpath[len(SHARED_PREFIX):]
        folder, sep, _inner = rest.partition("/")
        if not sep or folder not in SHARED_SUBFOLDERS:
            raise ValueError(f"path out of drive-sync scope: {relpath!r}")
        return _ENCODED_SHARED_PREFIX + rest.replace("/", "__")
    if not relpath.startswith(MEMORY_PREFIX):
        raise ValueError(f"path out of drive-sync scope: {relpath!r}")
    return _ENCODED_MEMORY_PREFIX + relpath[len(MEMORY_PREFIX):].replace("/", "__")


def decode_name(encoded: str) -> str | None:
    """Inverte ``encode_name``; ``None`` se il nome non è uno che sincronizziamo
    o se decodifica fuori dallo scope (traversal, path assoluto, ecc.).

    Un nome che non riconosciamo va **ignorato**, non rifiutato con errore: la
    cartella Drive è dell'utente, può contenere ``apex-sync-manifest.json`` o
    file suoi, e nessuno dei due deve né essere toccato né far fallire la sync.
    """
    if encoded in ROOT_SCOPE_FILES:
        return encoded
    if encoded.startswith(_ENCODED_SHARED_PREFIX):
        rest = encoded[len(_ENCODED_SHARED_PREFIX):]
        if not rest:
            return None
        # Deve esserci una sottocartella autorizzata *e* un nome file dopo di
        # essa: ``shared__profile`` da solo è la cartella, non un file.
        folder, sep, _inner = rest.partition("__")
        if not sep or folder not in SHARED_SUBFOLDERS or not _inner:
            return None
        relpath = SHARED_PREFIX + rest.replace("__", "/")
        return relpath if is_safe_scope_relpath(relpath) else None
    if encoded.startswith(_ENCODED_MEMORY_PREFIX):
        rest = encoded[len(_ENCODED_MEMORY_PREFIX):]
        if not rest:
            return None
        relpath = MEMORY_PREFIX + rest.replace("__", "/")
        return relpath if is_safe_scope_relpath(relpath) else None
    return None


def is_safe_scope_relpath(relpath: str) -> bool:
    """Vero se ``relpath`` sta dentro lo scope e non può uscire dal workspace."""
    if relpath in ROOT_SCOPE_FILES:
        return True
    if "\\" in relpath:
        return False
    if relpath.startswith(SHARED_PREFIX):
        sub = relpath[len(SHARED_PREFIX):]
        if not sub:
            return False
        parts = sub.split("/")
        return parts[0] in SHARED_SUBFOLDERS and not any(
            part in ("", ".", "..") for part in parts
        )
    if not relpath.startswith(MEMORY_PREFIX):
        return False
    sub = relpath[len(MEMORY_PREFIX):]
    if not sub:
        return False
    parts = sub.split("/")
    return not any(part in ("", ".", "..") for part in parts)


def is_shared_encoded_name(encoded: str) -> bool:
    """Vero per i nomi codificati dello scope condiviso (``shared__...``)."""
    return encoded.startswith(_ENCODED_SHARED_PREFIX)


def split_shared_encoded(encoded: str) -> tuple[str, str] | None:
    """Da un nome codificato condiviso alla coppia (sottocartella, nome remoto):
    ``shared__profile__USER.md`` -> ``("profile", "USER.md")``; ``None`` per
    qualunque nome che non appartenga allo scope condiviso.

    Il nome remoto è il file dentro la sottocartella Drive: un ``__`` residuo
    (``a__b.md``) sta per una sottocartella locale ``a/b.md``, la stessa
    convenzione di ``memory/``. Serve a chi orchestra per instradare le
    chiamate *In sul bridge.
    """
    relpath = decode_name(encoded)
    if relpath is None or not relpath.startswith(SHARED_PREFIX):
        return None
    folder, sep, rest = relpath[len(SHARED_PREFIX):].partition("/")
    if not sep or folder not in SHARED_SUBFOLDERS or not rest:
        return None
    return folder, rest.replace("/", "__")


def plan_sync(
    local: dict[str, FileMeta],
    remote: dict[str, FileMeta],
    manifest: dict[str, FileMeta],
) -> SyncPlan:
    """Decide upload/download/delete/no-op per ogni nome codificato coinvolto.

    ``remote`` deve arrivare già risolto (sha reale, non solo mtime): chi
    orchestra scarica per calcolarlo quando l'mtime remoto non combacia con
    quanto il manifest teneva in cache, altrimenti riusa lo sha del manifest
    senza rete. Qui dentro non c'è I/O, solo confronto.

    Regole (v. design del task):
      - solo locale -> upload.
      - solo remoto: se il nome era nel manifest e il remoto non è cambiato da
        allora (``remote.mtime <= manifest.mtime``) -> era nostro, l'utente lo
        ha cancellato in locale -> tombstone, cancella dal remoto. Altrimenti
        (nome nuovo o remoto cambiato dopo l'ultimo sync) -> download: nel
        dubbio si tiene il file remoto, mai si cancella.
      - in entrambi: sha uguale -> no-op; altrimenti vince l'mtime più recente;
        mtime pari e sha diverso -> vince il locale (tie-break documentato).
      - solo nel manifest (sparito da entrambi i lati) -> niente da fare, cade
        fuori dal prossimo manifest da solo.
    """
    uploads: list[str] = []
    downloads: list[str] = []
    deletes_remote: list[str] = []
    skipped: list[str] = []

    names = set(local) | set(remote) | set(manifest)
    for name in sorted(names):
        loc = local.get(name)
        rem = remote.get(name)
        man = manifest.get(name)

        if loc is not None and rem is None:
            uploads.append(name)
        elif loc is None and rem is not None:
            if man is not None and rem.mtime <= man.mtime:
                deletes_remote.append(name)
            else:
                downloads.append(name)
        elif loc is not None and rem is not None:
            if loc.sha256 == rem.sha256:
                skipped.append(name)
            elif loc.mtime > rem.mtime:
                uploads.append(name)
            elif rem.mtime > loc.mtime:
                downloads.append(name)
            else:
                uploads.append(name)  # mtime pari, sha diverso: vince il locale
        # else: solo nel manifest, niente da fare.

    return SyncPlan(
        uploads=tuple(uploads),
        downloads=tuple(downloads),
        deletes_remote=tuple(deletes_remote),
        skipped=tuple(skipped),
    )
