"""Test di orchestrazione per ``jenny.runtime.drive_sync.run_sync``.

Il bridge Android è sostituito con funzioni finte montate direttamente sui
nomi importati nel modulo (``drive_sync.drive_write_file`` ecc., non
``drive_sync_bridge.*``: sono binding diretti, v. l'``import ... from``
in testa a ``drive_sync.py``). L'algoritmo di decisione è già coperto in
isolamento da ``test_drive_sync_algorithm.py``; qui si copre che
l'orchestrazione lo alimenti correttamente e persista stato/manifest.
"""

from __future__ import annotations

import base64
import json

import pytest

import jenny.runtime.drive_sync as ds


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _ok_folder(name: str = "ApexSync"):
    async def _f():
        return {"ok": True, "name": name, "uri": "content://tree/x"}
    return _f


@pytest.mark.asyncio
async def test_run_sync_unavailable_outside_android(monkeypatch, tmp_path) -> None:
    async def _none():
        return None

    monkeypatch.setattr(ds, "drive_folder_info", _none)
    result = await ds.run_sync(tmp_path)
    assert result == {"ok": False, "error": "unavailable"}


@pytest.mark.asyncio
async def test_run_sync_no_folder_selected(monkeypatch, tmp_path) -> None:
    async def _no_folder():
        return {"ok": False, "error": "no_folder"}

    monkeypatch.setattr(ds, "drive_folder_info", _no_folder)
    result = await ds.run_sync(tmp_path)
    assert result == {"ok": False, "error": "no_folder"}


@pytest.mark.asyncio
async def test_first_sync_pushes_all_local_files(monkeypatch, tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("mem", encoding="utf-8")

    async def _list():
        return {"ok": True, "files": []}

    writes: dict[str, bytes] = {}

    async def _write(name, content_b64):
        writes[name] = base64.b64decode(content_b64)
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder())
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    assert sorted(result["pushed"]) == ["SOUL.md", "memory__MEMORY.md"]
    assert writes["SOUL.md"] == b"soul"
    assert writes["memory__MEMORY.md"] == b"mem"
    manifest = json.loads(writes[ds.MANIFEST_REMOTE_NAME])
    assert set(manifest["files"]) == {"SOUL.md", "memory__MEMORY.md"}

    state = json.loads((tmp_path / ".jenny" / "drive_sync_state.json").read_text("utf-8"))
    assert state["folder_name"] == "ApexSync"
    assert set(state["manifest_files"]) == {"SOUL.md", "memory__MEMORY.md"}
    assert state["last_sync"]["ok"] is True


@pytest.mark.asyncio
async def test_downloads_new_remote_file(monkeypatch, tmp_path) -> None:
    async def _list():
        return {"ok": True, "files": [{"name": "USER.md", "mtime": 123.0, "size": 5}]}

    async def _read(name):
        assert name == "USER.md"
        return {"ok": True, "content": _b64(b"hello")}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder())
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_read_file", _read)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["pulled"] == ["USER.md"]
    assert (tmp_path / "USER.md").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_unknown_remote_file_is_ignored_not_downloaded(monkeypatch, tmp_path) -> None:
    async def _list():
        return {"ok": True, "files": [{"name": "random-notes.txt", "mtime": 1.0, "size": 3}]}

    read_calls: list[str] = []

    async def _read(name):
        read_calls.append(name)
        return {"ok": True, "content": _b64(b"x")}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder())
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_read_file", _read)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["pulled"] == []
    assert read_calls == []  # mai scaricato: nome non riconosciuto
    assert not (tmp_path / "random-notes.txt").exists()


@pytest.mark.asyncio
async def test_tombstone_deletes_remote_when_local_file_removed(monkeypatch, tmp_path) -> None:
    # Stato di un sync precedente: SOUL.md era stato caricato da questo device.
    state_dir = tmp_path / ".jenny"
    state_dir.mkdir()
    (state_dir / "drive_sync_state.json").write_text(
        json.dumps({
            "device_id": "dev-1",
            "folder_name": "ApexSync",
            "manifest_files": {"SOUL.md": {"mtime": 100.0, "sha256": "abc"}},
        }),
        encoding="utf-8",
    )
    # SOUL.md non esiste più in locale (utente lo ha cancellato).

    async def _list():
        # Il remoto non è cambiato dall'ultimo sync: stesso mtime del manifest.
        return {"ok": True, "files": [{"name": "SOUL.md", "mtime": 100.0, "size": 3}]}

    deleted: list[str] = []

    async def _delete(name):
        deleted.append(name)
        return {"ok": True}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder())
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_delete_file", _delete)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["deleted"] == ["SOUL.md"]
    assert deleted == ["SOUL.md"]


@pytest.mark.asyncio
async def test_per_file_errors_do_not_abort_whole_sync(monkeypatch, tmp_path) -> None:
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    (tmp_path / "USER.md").write_text("user", encoding="utf-8")

    async def _list():
        return {"ok": True, "files": []}

    async def _write(name, content_b64):
        if name == "SOUL.md":
            return {"ok": False, "error": "write_failed"}
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder())
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is False
    assert "USER.md" in result["pushed"]
    assert any(e["file"] == "SOUL.md" for e in result["errors"])


@pytest.mark.asyncio
async def test_startup_sync_skips_when_disabled(monkeypatch, tmp_path) -> None:
    from jenny.config.schema import Config

    config = Config()
    config.drive_sync.enabled = False
    monkeypatch.setattr("jenny.config.loader.load_config", lambda: config)

    called = False

    async def _run_sync(workspace):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(ds, "run_sync", _run_sync)
    await ds.run_startup_sync(tmp_path)
    assert called is False


@pytest.mark.asyncio
async def test_startup_sync_never_raises_on_failure(monkeypatch, tmp_path) -> None:
    from jenny.config.schema import Config

    monkeypatch.setattr("jenny.config.loader.load_config", lambda: Config())

    async def _boom(workspace):
        raise RuntimeError("network down")

    monkeypatch.setattr(ds, "run_sync", _boom)
    await ds.run_startup_sync(tmp_path)  # non deve sollevare


# ── scope condiviso (Apex-Pamyat) ────────────────────────────────────────


async def _shared_calls_fail(*args, **kwargs):
    raise AssertionError(f"shared scope chiamato per cartella non condivisa: {args!r}")


@pytest.mark.asyncio
async def test_sync_status_flags_shared_folder(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat"))
    status = await ds.sync_status(tmp_path)
    assert status["shared_active"] is True
    assert status["folder"]["name"] == "Apex-Pamyat"

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Personal"))
    status = await ds.sync_status(tmp_path)
    assert status["shared_active"] is False

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat-but-not"))
    status = await ds.sync_status(tmp_path)
    assert status["shared_active"] is False


@pytest.mark.asyncio
async def test_non_shared_folder_leaves_shared_scope_untouched(monkeypatch, tmp_path) -> None:
    """Cartella scelta != Apex-Pamyat: i file locali di shared/ restano locali,
    nessuna chiamata *In, nessuna sottocartella creata, manifest senza shared__."""
    (tmp_path / "SOUL.md").write_text("soul", encoding="utf-8")
    shared_notes = tmp_path / "shared" / "notes"
    shared_notes.mkdir(parents=True)
    (shared_notes / "apex-phone-x.md").write_text("local note", encoding="utf-8")

    async def _list():
        return {"ok": True, "files": []}

    writes: dict[str, bytes] = {}

    async def _write(name, content_b64):
        writes[name] = base64.b64decode(content_b64)
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Personal"))
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_write_file", _write)
    for name in (
        "drive_ensure_folder",
        "drive_list_files_in",
        "drive_read_file_in",
        "drive_write_file_in",
        "drive_delete_file_in",
    ):
        monkeypatch.setattr(ds, name, _shared_calls_fail)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    # Il file shared locale non viene caricato (né in root né in sottocartella).
    assert set(writes) == {ds.MANIFEST_REMOTE_NAME, "SOUL.md"}
    manifest = json.loads(writes[ds.MANIFEST_REMOTE_NAME])
    assert set(manifest["files"]) == {"SOUL.md"}
    # Nessuna nuova sottocartella creata accanto a quelle locali già presenti.
    assert not (tmp_path / "shared" / "profile").exists()
    assert not (tmp_path / "shared" / "knowledge").exists()
    state = json.loads((tmp_path / ".jenny" / "drive_sync_state.json").read_text("utf-8"))
    assert not any(k.startswith("shared__") for k in state["manifest_files"])


@pytest.mark.asyncio
async def test_apex_pamyat_syncs_both_directions_in_subfolders(monkeypatch, tmp_path) -> None:
    """Cartella == Apex-Pamyat: il file locale shared/notes viene caricato nella
    sottocartella reale (ensureFolder solo quando c'è da scrivere) e il file del
    PC in profile/ viene scaricato nel mirror locale shared/profile/."""
    shared_notes = tmp_path / "shared" / "notes"
    shared_notes.mkdir(parents=True)
    (shared_notes / "apex-phone-2026-09-03.md").write_text("from phone", encoding="utf-8")

    async def _list():
        # Il manifest remoto in root è ignorato come sempre.
        return {"ok": True, "files": [{"name": "apex-sync-manifest.json", "mtime": 1.0, "size": 3}]}

    async def _list_in(folder):
        if folder == "profile":
            return {"ok": True, "files": [{"name": "USER.md", "mtime": 100.0, "size": 7}]}
        return {"ok": True, "files": []}

    async def _read_in(folder, name):
        assert (folder, name) == ("profile", "USER.md")
        return {"ok": True, "content": _b64(b"pc-user")}

    ensured: list[str] = []
    writes_in: dict[tuple[str, str], bytes] = {}

    async def _ensure(folder):
        ensured.append(folder)
        return {"ok": True}

    async def _write_in(folder, name, content_b64):
        writes_in[(folder, name)] = base64.b64decode(content_b64)
        return {"ok": True}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat"))
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_list_files_in", _list_in)
    monkeypatch.setattr(ds, "drive_read_file_in", _read_in)
    monkeypatch.setattr(ds, "drive_ensure_folder", _ensure)
    monkeypatch.setattr(ds, "drive_write_file_in", _write_in)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    assert result["pushed"] == ["shared__notes__apex-phone-2026-09-03.md"]
    assert result["pulled"] == ["shared__profile__USER.md"]
    # ensureFolder solo per la sottocartella in cui c'è da scrivere.
    assert ensured == ["notes"]
    assert writes_in == {
        ("notes", "apex-phone-2026-09-03.md"): b"from phone",
    }
    # Il file del PC è arrivato nel mirror locale.
    assert (tmp_path / "shared" / "profile" / "USER.md").read_bytes() == b"pc-user"
    # Il manifest (stato salvato) copre i nomi shared__ di entrambe le direzioni.
    state = json.loads((tmp_path / ".jenny" / "drive_sync_state.json").read_text("utf-8"))
    assert set(state["manifest_files"]) == {
        "shared__notes__apex-phone-2026-09-03.md",
        "shared__profile__USER.md",
    }


@pytest.mark.asyncio
async def test_apex_pamyat_missing_subfolder_is_empty_not_error(monkeypatch, tmp_path) -> None:
    """Una sottocartella remota mai creata (not_found) non è un errore: il
    contenuto locale ci viene caricato e la sottocartella nasce al primo write."""
    shared_profile = tmp_path / "shared" / "profile"
    shared_profile.mkdir(parents=True)
    (shared_profile / "USER.md").write_text("shared profile", encoding="utf-8")

    async def _list():
        return {"ok": True, "files": []}

    async def _list_in(folder):
        return {"ok": False, "error": "not_found"}

    ensured: list[str] = []
    writes_in: dict[tuple[str, str], bytes] = {}

    async def _ensure(folder):
        ensured.append(folder)
        return {"ok": True}

    async def _write_in(folder, name, content_b64):
        writes_in[(folder, name)] = base64.b64decode(content_b64)
        return {"ok": True}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat"))
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_list_files_in", _list_in)
    monkeypatch.setattr(ds, "drive_ensure_folder", _ensure)
    monkeypatch.setattr(ds, "drive_write_file_in", _write_in)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["pushed"] == ["shared__profile__USER.md"]
    assert ensured == ["profile"]
    assert writes_in == {("profile", "USER.md"): b"shared profile"}


@pytest.mark.asyncio
async def test_apex_pamyat_ignores_hostile_names_in_subfolder(monkeypatch, tmp_path) -> None:
    """Un nome remoto che decodifica fuori dallo scope (traversal via __) nella
    lista di una sottocartella viene ignorato, mai scaricato né scritto."""
    async def _list():
        return {"ok": True, "files": []}

    async def _list_in(folder):
        if folder == "notes":
            return {"ok": True, "files": [{"name": "..__evil.md", "mtime": 1.0, "size": 2}]}
        return {"ok": True, "files": []}

    read_calls: list[tuple[str, str]] = []

    async def _read_in(folder, name):
        read_calls.append((folder, name))
        return {"ok": True, "content": _b64(b"x")}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat"))
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_list_files_in", _list_in)
    monkeypatch.setattr(ds, "drive_read_file_in", _read_in)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    assert read_calls == []
    assert result["pulled"] == []
    assert not (tmp_path / "shared").exists() or not (tmp_path / "evil.md").exists()


@pytest.mark.asyncio
async def test_apex_pamyat_tombstone_deletes_shared_file_in_subfolder(monkeypatch, tmp_path) -> None:
    """File condiviso cancellato in locale e invariato sul remoto (mtime ==
    manifest) => tombstone via deleteFileIn nella sottocartella giusta."""
    state_dir = tmp_path / ".jenny"
    state_dir.mkdir()
    (state_dir / "drive_sync_state.json").write_text(
        json.dumps({
            "device_id": "dev-1",
            "folder_name": "Apex-Pamyat",
            "manifest_files": {
                "shared__notes__apex-phone-old.md": {"mtime": 100.0, "sha256": "abc"}
            },
        }),
        encoding="utf-8",
    )

    async def _list():
        return {"ok": True, "files": []}

    async def _list_in(folder):
        if folder == "notes":
            return {"ok": True, "files": [{"name": "apex-phone-old.md", "mtime": 100.0, "size": 3}]}
        return {"ok": True, "files": []}

    deleted_in: list[tuple[str, str]] = []

    async def _delete_in(folder, name):
        deleted_in.append((folder, name))
        return {"ok": True}

    async def _write(name, content_b64):
        return {"ok": True}

    monkeypatch.setattr(ds, "drive_folder_info", _ok_folder("Apex-Pamyat"))
    monkeypatch.setattr(ds, "drive_list_files", _list)
    monkeypatch.setattr(ds, "drive_list_files_in", _list_in)
    monkeypatch.setattr(ds, "drive_delete_file_in", _delete_in)
    monkeypatch.setattr(ds, "drive_write_file", _write)

    result = await ds.run_sync(tmp_path)

    assert result["ok"] is True
    assert result["deleted"] == ["shared__notes__apex-phone-old.md"]
    assert deleted_in == [("notes", "apex-phone-old.md")]
