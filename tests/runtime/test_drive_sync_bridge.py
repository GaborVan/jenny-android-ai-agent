"""Test per ``jenny/runtime/drive_sync_bridge.py``.

Fuori da Android tutto degrada a no-op senza sollevare (``None``); con un
context finto e il bridge finto montato sul seam ``_resolve_bridge_class``, i
risultati JSON del bridge arrivano parsati al chiamante. Stesso pattern di
``test_notifications_clipboard.py``.
"""

from __future__ import annotations

import pytest

import jenny.runtime.drive_sync_bridge as drive_sync_bridge


@pytest.mark.asyncio
async def test_all_ops_none_without_android_context(monkeypatch) -> None:
    monkeypatch.setattr(drive_sync_bridge, "get_android_context", lambda: None)
    assert await drive_sync_bridge.drive_folder_info() is None
    assert await drive_sync_bridge.drive_list_files() is None
    assert await drive_sync_bridge.drive_read_file("SOUL.md") is None
    assert await drive_sync_bridge.drive_write_file("SOUL.md", "aGk=") is None
    assert await drive_sync_bridge.drive_delete_file("SOUL.md") is None
    assert await drive_sync_bridge.drive_ensure_folder("notes") is None
    assert await drive_sync_bridge.drive_list_files_in("notes") is None
    assert await drive_sync_bridge.drive_read_file_in("notes", "x.md") is None
    assert await drive_sync_bridge.drive_write_file_in("notes", "x.md", "aGk=") is None
    assert await drive_sync_bridge.drive_delete_file_in("notes", "x.md") is None


class _FakeBridge:
    def __init__(self, context: object | None = None) -> None:
        self._context = context

    def getFolderInfo(self) -> str:  # noqa: N802
        return '{"ok":true,"name":"ApexSync","uri":"content://tree/x"}'

    def listFiles(self) -> str:  # noqa: N802
        return '{"ok":true,"files":[{"name":"SOUL.md","mtime":100.0,"size":4}]}'

    def readFile(self, name: str) -> str:  # noqa: N802
        return '{"ok":true,"content":"aGVsbG8="}'

    def writeFile(self, name: str, content_b64: str) -> str:  # noqa: N802
        return '{"ok":true}'

    def deleteFile(self, name: str) -> str:  # noqa: N802
        return '{"ok":true}'

    # Scope condiviso: sottocartelle reali.
    def ensureFolder(self, folder: str) -> str:  # noqa: N802
        return '{"ok":true}'

    def listFilesIn(self, folder: str) -> str:  # noqa: N802
        return '{"ok":true,"files":[{"name":"USER.md","mtime":100.0,"size":4}]}'

    def readFileIn(self, folder: str, name: str) -> str:  # noqa: N802
        return '{"ok":true,"content":"aGVsbG8="}'

    def writeFileIn(self, folder: str, name: str, content_b64: str) -> str:  # noqa: N802
        return '{"ok":true}'

    def deleteFileIn(self, folder: str, name: str) -> str:  # noqa: N802
        return '{"ok":true}'


@pytest.fixture(autouse=True)
def _fake_bridge(monkeypatch):
    context = object()
    monkeypatch.setattr(drive_sync_bridge, "get_android_context", lambda: context)
    monkeypatch.setattr(drive_sync_bridge, "_resolve_bridge_class", lambda: _FakeBridge)
    drive_sync_bridge.reset_drive_sync_bridge_state()
    yield
    drive_sync_bridge.reset_drive_sync_bridge_state()


@pytest.mark.asyncio
async def test_folder_info_parses_bridge_json() -> None:
    result = await drive_sync_bridge.drive_folder_info()
    assert result == {"ok": True, "name": "ApexSync", "uri": "content://tree/x"}


@pytest.mark.asyncio
async def test_list_files_parses_bridge_json() -> None:
    result = await drive_sync_bridge.drive_list_files()
    assert result["ok"] is True
    assert result["files"][0]["name"] == "SOUL.md"


@pytest.mark.asyncio
async def test_read_write_delete_parse_bridge_json() -> None:
    read = await drive_sync_bridge.drive_read_file("SOUL.md")
    assert read == {"ok": True, "content": "aGVsbG8="}

    written = await drive_sync_bridge.drive_write_file("SOUL.md", "aGVsbG8=")
    assert written == {"ok": True}

    deleted = await drive_sync_bridge.drive_delete_file("SOUL.md")
    assert deleted == {"ok": True}


@pytest.mark.asyncio
async def test_subfolder_ops_parse_bridge_json() -> None:
    ensured = await drive_sync_bridge.drive_ensure_folder("notes")
    assert ensured == {"ok": True}

    listed = await drive_sync_bridge.drive_list_files_in("notes")
    assert listed["ok"] is True
    assert listed["files"][0]["name"] == "USER.md"

    read = await drive_sync_bridge.drive_read_file_in("notes", "USER.md")
    assert read == {"ok": True, "content": "aGVsbG8="}

    written = await drive_sync_bridge.drive_write_file_in("notes", "x.md", "aGVsbG8=")
    assert written == {"ok": True}

    deleted = await drive_sync_bridge.drive_delete_file_in("notes", "x.md")
    assert deleted == {"ok": True}


@pytest.mark.asyncio
async def test_subfolder_ops_degrade_on_bridge_failure(monkeypatch) -> None:
    class _BrokenBridge:
        def __init__(self, context: object | None = None) -> None:
            pass

        def listFilesIn(self, folder: str) -> str:  # noqa: N802
            raise RuntimeError("boom")

    monkeypatch.setattr(drive_sync_bridge, "_resolve_bridge_class", lambda: _BrokenBridge)
    drive_sync_bridge.reset_drive_sync_bridge_state()
    result = await drive_sync_bridge.drive_list_files_in("notes")
    assert result == {"ok": False, "error": "bridge_unavailable"}


@pytest.mark.asyncio
async def test_non_json_reply_degrades_to_bridge_unavailable(monkeypatch) -> None:
    class _BadBridge:
        def __init__(self, context: object | None = None) -> None:
            pass

        def getFolderInfo(self) -> str:  # noqa: N802
            return "not json"

    monkeypatch.setattr(drive_sync_bridge, "_resolve_bridge_class", lambda: _BadBridge)
    drive_sync_bridge.reset_drive_sync_bridge_state()
    result = await drive_sync_bridge.drive_folder_info()
    assert result == {"ok": False, "error": "bridge_unavailable"}
