"""Test della logica pura di ``jenny.runtime.drive_sync_algorithm``.

Nessun I/O: ``plan_sync`` decide solo a partire da dizionari sintetici, ed è
qui che va la copertura delle regole di decisione (last-writer-wins, tombstone,
tie-break, traversal). Gli adapter (I/O vero) sono coperti separatamente in
``test_drive_sync_local.py`` e ``test_drive_sync_bridge.py``.
"""

from __future__ import annotations

from jenny.runtime.drive_sync_algorithm import (
    FileMeta,
    decode_name,
    encode_name,
    is_safe_scope_relpath,
    plan_sync,
)

# ── encode/decode name ──────────────────────────────────────────────────────


def test_encode_root_files_unchanged() -> None:
    assert encode_name("SOUL.md") == "SOUL.md"
    assert encode_name("USER.md") == "USER.md"


def test_encode_memory_file_flattens_slashes() -> None:
    assert encode_name("memory/MEMORY.md") == "memory__MEMORY.md"
    assert encode_name("memory/2026-09-02.md") == "memory__2026-09-02.md"
    assert encode_name("memory/sub/dir/note.md") == "memory__sub__dir__note.md"


def test_decode_reverses_encode_for_valid_names() -> None:
    for relpath in ("SOUL.md", "USER.md", "memory/MEMORY.md", "memory/sub/dir/note.md"):
        assert decode_name(encode_name(relpath)) == relpath


def test_decode_rejects_unknown_names() -> None:
    # Nomi che non seguono la nostra convenzione (il manifest stesso, file
    # dell'utente): vanno ignorati, non rifiutati con errore — v. docstring.
    assert decode_name("apex-sync-manifest.json") is None
    assert decode_name("random-file.txt") is None
    assert decode_name("") is None
    assert decode_name("memory__") is None


def test_decode_rejects_path_traversal() -> None:
    assert decode_name("memory__..__..") is None  # -> "memory/../.."
    assert decode_name("memory__..__etc__passwd") is None
    assert is_safe_scope_relpath("memory/../../etc/passwd") is False
    assert is_safe_scope_relpath("../SOUL.md") is False
    assert is_safe_scope_relpath("/etc/passwd") is False


def test_decode_rejects_absolute_and_backslash_paths() -> None:
    assert is_safe_scope_relpath("memory/..\\..\\windows") is False


# ── plan_sync ────────────────────────────────────────────────────────────


def _meta(mtime: float, sha: str = "a") -> FileMeta:
    return FileMeta(mtime=mtime, sha256=sha)


def test_first_sync_pushes_everything_local_only() -> None:
    local = {"SOUL.md": _meta(100.0), "memory__MEMORY.md": _meta(50.0)}
    plan = plan_sync(local, remote={}, manifest={})
    assert set(plan.uploads) == {"SOUL.md", "memory__MEMORY.md"}
    assert plan.downloads == ()
    assert plan.deletes_remote == ()
    assert plan.skipped == ()


def test_remote_only_new_file_downloads() -> None:
    remote = {"USER.md": _meta(10.0, "x")}
    plan = plan_sync(local={}, remote=remote, manifest={})
    assert plan.downloads == ("USER.md",)
    assert plan.uploads == ()
    assert plan.deletes_remote == ()


def test_both_sides_local_newer_wins() -> None:
    local = {"SOUL.md": _meta(200.0, "local-sha")}
    remote = {"SOUL.md": _meta(100.0, "remote-sha")}
    plan = plan_sync(local, remote, manifest={})
    assert plan.uploads == ("SOUL.md",)
    assert plan.downloads == ()


def test_both_sides_remote_newer_wins() -> None:
    local = {"SOUL.md": _meta(100.0, "local-sha")}
    remote = {"SOUL.md": _meta(200.0, "remote-sha")}
    plan = plan_sync(local, remote, manifest={})
    assert plan.downloads == ("SOUL.md",)
    assert plan.uploads == ()


def test_both_sides_identical_content_is_noop() -> None:
    local = {"SOUL.md": _meta(100.0, "same-sha")}
    remote = {"SOUL.md": _meta(999.0, "same-sha")}  # mtime diverso, sha uguale
    plan = plan_sync(local, remote, manifest={})
    assert plan.skipped == ("SOUL.md",)
    assert plan.uploads == ()
    assert plan.downloads == ()


def test_equal_mtime_different_sha_local_wins_tiebreak() -> None:
    local = {"SOUL.md": _meta(100.0, "local-sha")}
    remote = {"SOUL.md": _meta(100.0, "remote-sha")}
    plan = plan_sync(local, remote, manifest={})
    assert plan.uploads == ("SOUL.md",)
    assert plan.downloads == ()


def test_tombstone_deletes_remote_when_local_deleted_and_remote_unchanged() -> None:
    # Il device ha sincronizzato SOUL.md in precedenza (è nel manifest),
    # l'utente lo ha cancellato in locale, e il remoto non è cambiato da
    # allora (mtime remoto == mtime che avevamo in manifest).
    manifest = {"SOUL.md": _meta(100.0, "sha")}
    remote = {"SOUL.md": _meta(100.0, "sha")}
    plan = plan_sync(local={}, remote=remote, manifest=manifest)
    assert plan.deletes_remote == ("SOUL.md",)
    assert plan.downloads == ()


def test_tombstone_does_not_fire_when_remote_changed_after_manifest() -> None:
    # Altro dispositivo ha aggiornato il file DOPO il nostro ultimo sync: nel
    # dubbio si tiene il remoto, mai lo si cancella per una nostra cancellazione
    # locale ormai superata.
    manifest = {"SOUL.md": _meta(100.0, "old-sha")}
    remote = {"SOUL.md": _meta(150.0, "new-sha")}
    plan = plan_sync(local={}, remote=remote, manifest=manifest)
    assert plan.downloads == ("SOUL.md",)
    assert plan.deletes_remote == ()


def test_stale_manifest_only_entry_is_noop() -> None:
    # Sparito da entrambi i lati: niente da fare, cadrà fuori dal prossimo
    # manifest da solo (chi orchestra lo ricostruisce dallo stato finale).
    manifest = {"SOUL.md": _meta(100.0, "sha")}
    plan = plan_sync(local={}, remote={}, manifest=manifest)
    assert plan.uploads == ()
    assert plan.downloads == ()
    assert plan.deletes_remote == ()
    assert plan.skipped == ()


def test_plan_is_deterministic_sorted_output() -> None:
    local = {"USER.md": _meta(1.0), "SOUL.md": _meta(1.0), "memory__b.md": _meta(1.0)}
    plan = plan_sync(local, remote={}, manifest={})
    assert plan.uploads == ("SOUL.md", "USER.md", "memory__b.md")
