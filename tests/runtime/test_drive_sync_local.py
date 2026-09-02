"""Test dell'adapter locale ``jenny.runtime.drive_sync_local`` (I/O su ``Path``,
nessuna decisione — quella è in ``drive_sync_algorithm``)."""

from __future__ import annotations

import hashlib

from jenny.runtime.drive_sync_local import (
    read_scope_file,
    resolve_scope_path,
    scope_snapshot,
    write_scope_file,
)


def test_scope_snapshot_picks_up_root_and_memory_files(tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    (tmp_path / "USER.md").write_text("user", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("mem", encoding="utf-8")
    sub = memory / "notes"
    sub.mkdir()
    (sub / "2026-09-02.md").write_text("note", encoding="utf-8")
    # Fuori scope: non deve comparire.
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "x.md").write_text("skill", encoding="utf-8")

    snapshot = scope_snapshot(tmp_path)

    assert set(snapshot) == {"SOUL.md", "USER.md", "memory__MEMORY.md", "memory__notes__2026-09-02.md"}
    assert snapshot["SOUL.md"].sha256 == hashlib.sha256(b"soul").hexdigest()


def test_scope_snapshot_empty_workspace(tmp_path) -> None:
    assert scope_snapshot(tmp_path) == {}


def test_read_write_round_trip(tmp_path) -> None:
    written = write_scope_file(tmp_path, "memory__MEMORY.md", b"hello world")
    assert written == tmp_path / "memory" / "MEMORY.md"
    assert written.read_bytes() == b"hello world"
    assert read_scope_file(tmp_path, "memory__MEMORY.md") == b"hello world"


def test_write_creates_missing_memory_subdirs(tmp_path) -> None:
    write_scope_file(tmp_path, "memory__sub__dir__note.md", b"data")
    assert (tmp_path / "memory" / "sub" / "dir" / "note.md").read_bytes() == b"data"


def test_read_missing_file_returns_none(tmp_path) -> None:
    assert read_scope_file(tmp_path, "SOUL.md") is None


def test_resolve_rejects_unknown_or_unsafe_names(tmp_path) -> None:
    assert resolve_scope_path(tmp_path, "config.json") is None
    assert resolve_scope_path(tmp_path, "apex-sync-manifest.json") is None
    assert resolve_scope_path(tmp_path, "memory__..__..__etc__passwd") is None


def test_write_rejects_unsafe_name(tmp_path) -> None:
    assert write_scope_file(tmp_path, "../outside.md", b"x") is None
    # Nessun file scritto fuori dal workspace.
    assert not (tmp_path.parent / "outside.md").exists()
