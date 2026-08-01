"""Tipi condivisi del motore di snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Trigger riconosciuti per uno snapshot. La stringa finisce nel manifest e
# nella UI (badge tradotto via i18n lato client).
SNAPSHOT_TRIGGERS = (
    "auto",
    "daily",
    "pre_dream",
    "shutdown",
    "pre_restore",
    "pre_export",
    "import",
    "manual",
)


@dataclass
class FileEntry:
    """Un file tracciato dentro uno snapshot.

    ``path`` è sempre relativo alla radice del workspace, in forma POSIX
    (separatore ``/``), così i manifest sono portabili tra device.
    """

    path: str
    hash: str
    size: int
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "hash": self.hash,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileEntry:
        return cls(
            path=str(data["path"]),
            hash=str(data["hash"]),
            size=int(data["size"]),
            mtime_ns=int(data.get("mtime_ns", 0)),
        )


@dataclass
class SnapshotManifest:
    """Uno snapshot completo del workspace (l'equivalente di un commit)."""

    id: str
    created_at_ms: int
    trigger: str
    label: str | None = None
    parent: str | None = None
    files: list[FileEntry] = field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at_ms": self.created_at_ms,
            "trigger": self.trigger,
            "label": self.label,
            "parent": self.parent,
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotManifest:
        return cls(
            id=str(data["id"]),
            created_at_ms=int(data["created_at_ms"]),
            trigger=str(data.get("trigger", "auto")),
            label=data.get("label"),
            parent=data.get("parent"),
            files=[FileEntry.from_dict(f) for f in data.get("files", [])],
        )

    def summary(self) -> dict[str, Any]:
        """Riga compatta per l'indice e per la lista snapshot della UI."""
        return {
            "id": self.id,
            "created_at_ms": self.created_at_ms,
            "trigger": self.trigger,
            "label": self.label,
            "parent": self.parent,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }
