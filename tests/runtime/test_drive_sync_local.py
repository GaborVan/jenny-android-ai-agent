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


def test_scope_snapshot_includes_shared_tree(tmp_path) -> None:
    shared = tmp_path / "shared"
    (shared / "profile").mkdir(parents=True)
    (shared / "profile" / "USER.md").write_text("shared user", encoding="utf-8")
    (shared / "knowledge").mkdir()
    (shared / "knowledge" / "instances.md").write_text("instances", encoding="utf-8")
    (shared / "notes").mkdir()
    (shared / "notes" / "apex-phone-2026-09-03.md").write_text("note", encoding="utf-8")
    nested = shared / "knowledge" / "sub"
    nested.mkdir()
    (nested / "deep.md").write_text("deep", encoding="utf-8")
    # Fuori dalle sottocartelle autorizzate: non deve comparire.
    (shared / "other").mkdir()
    (shared / "other" / "x.md").write_text("x", encoding="utf-8")
    (shared / "stray.md").write_text("stray", encoding="utf-8")

    snapshot = scope_snapshot(tmp_path)

    assert set(snapshot) == {
        "shared__profile__USER.md",
        "shared__knowledge__instances.md",
        "shared__notes__apex-phone-2026-09-03.md",
        "shared__knowledge__sub__deep.md",
    }
    assert "shared__other__x.md" not in snapshot
    assert "shared__stray.md" not in snapshot


def test_scope_snapshot_merges_root_memory_and_shared(tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("mem", encoding="utf-8")
    (tmp_path / "shared" / "notes").mkdir(parents=True)
    (tmp_path / "shared" / "notes" / "apex-phone-x.md").write_text("n", encoding="utf-8")

    snapshot = scope_snapshot(tmp_path)
    assert set(snapshot) == {
        "SOUL.md",
        "memory__MEMORY.md",
        "shared__notes__apex-phone-x.md",
    }


def test_read_write_round_trip(tmp_path) -> None:
    written = write_scope_file(tmp_path, "memory__MEMORY.md", b"hello world")
    assert written == tmp_path / "memory" / "MEMORY.md"
    assert written.read_bytes() == b"hello world"
    assert read_scope_file(tmp_path, "memory__MEMORY.md") == b"hello world"


def test_write_creates_missing_memory_subdirs(tmp_path) -> None:
    write_scope_file(tmp_path, "memory__sub__dir__note.md", b"data")
    assert (tmp_path / "memory" / "sub" / "dir" / "note.md").read_bytes() == b"data"


def test_shared_write_read_round_trip(tmp_path) -> None:
    written = write_scope_file(tmp_path, "shared__profile__USER.md", b"shared user")
    assert written == tmp_path / "shared" / "profile" / "USER.md"
    assert written.read_bytes() == b"shared user"
    assert read_scope_file(tmp_path, "shared__profile__USER.md") == b"shared user"


def test_shared_write_creates_missing_subdirs(tmp_path) -> None:
    write_scope_file(tmp_path, "shared__knowledge__sub__deep.md", b"data")
    assert (tmp_path / "shared" / "knowledge" / "sub" / "deep.md").read_bytes() == b"data"


def test_shared_nested_round_trip_matches_remote_flat_name(tmp_path) -> None:
    # Il nome remoto (``a__b.md`` in una sottocartella) e il mirror locale
    # annidato (``a/b.md``) sono la stessa voce.
    write_scope_file(tmp_path, "shared__notes__a__b.md", b"flat")
    assert (tmp_path / "shared" / "notes" / "a" / "b.md").read_bytes() == b"flat"
    assert read_scope_file(tmp_path, "shared__notes__a__b.md") == b"flat"


def test_read_missing_file_returns_none(tmp_path) -> None:
    assert read_scope_file(tmp_path, "SOUL.md") is None


def test_resolve_rejects_unknown_or_unsafe_names(tmp_path) -> None:
    assert resolve_scope_path(tmp_path, "config.json") is None
    assert resolve_scope_path(tmp_path, "apex-sync-manifest.json") is None
    assert resolve_scope_path(tmp_path, "memory__..__..__etc__passwd") is None
    assert resolve_scope_path(tmp_path, "shared__other__x.md") is None
    assert resolve_scope_path(tmp_path, "shared__profile") is None
    assert resolve_scope_path(tmp_path, "shared__..__..") is None
    assert resolve_scope_path(tmp_path, "shared__profile__..__evil.md") is None


def test_shared_unsafe_writes_go_nowhere(tmp_path) -> None:
    assert write_scope_file(tmp_path, "shared__other__x.md", b"x") is None
    assert write_scope_file(tmp_path, "shared__profile__..__evil.md", b"x") is None
    assert read_scope_file(tmp_path, "shared__profile__..__evil.md") is None
    # Nessun file scritto fuori dalle sottocartelle autorizzate.
    assert not (tmp_path / "evil.md").exists()
    assert not (tmp_path / "shared" / "other").exists()


def test_write_rejects_unsafe_name(tmp_path) -> None:
    assert write_scope_file(tmp_path, "../outside.md", b"x") is None
    # Nessun file scritto fuori dal workspace.
    assert not (tmp_path.parent / "outside.md").exists()
