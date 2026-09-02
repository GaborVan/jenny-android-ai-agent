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
