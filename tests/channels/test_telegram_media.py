"""Test per ``jenny.channels.telegram_media.download_telegram_media``.

Isolato dal canale: qui si verifica solo la logica di download/persistenza
contro un doppio minimale di ``TelegramAPI`` (get_file/download_file), non il
routing degli update Telegram (coperto in ``test_telegram_channel.py``).
"""

from __future__ import annotations

from typing import Any

from jenny.channels.telegram_api import TelegramAPIError
from jenny.channels.telegram_media import MAX_DOWNLOAD_BYTES, download_telegram_media


class FakeAPI:
    def __init__(self) -> None:
        self.file_info: dict[str, dict[str, Any]] = {}
        self.file_bytes: dict[str, bytes] = {}
        self.file_errors: dict[str, TelegramAPIError] = {}
        self.download_errors: dict[str, TelegramAPIError] = {}
        self.download_calls: list[tuple[str, int]] = []

    async def get_file(self, file_id: str) -> dict[str, Any]:
        if file_id in self.file_errors:
            raise self.file_errors[file_id]
        return self.file_info[file_id]

    async def download_file(self, file_path: str, *, max_bytes: int) -> bytes:
        self.download_calls.append((file_path, max_bytes))
        if file_path in self.download_errors:
            raise self.download_errors[file_path]
        return self.file_bytes[file_path]


async def test_downloads_and_persists_under_media_dir(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["f1"] = {"file_id": "f1", "file_path": "photos/f1.jpg", "file_size": 3}
    api.file_bytes["photos/f1.jpg"] = b"abc"

    result = await download_telegram_media(api, "f1")

    assert result == tmp_path / "f1.jpg"
    assert result.read_bytes() == b"abc"


async def test_suggested_name_wins_over_file_path_extension(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["f2"] = {"file_id": "f2", "file_path": "documents/file_2.bin"}
    api.file_bytes["documents/file_2.bin"] = b"data"

    result = await download_telegram_media(api, "f2", suggested_name="report.pdf")

    assert result.suffix == ".pdf"


async def test_get_file_error_returns_none(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_errors["missing"] = TelegramAPIError(400, "file not found")

    result = await download_telegram_media(api, "missing")

    assert result is None
    assert list(tmp_path.iterdir()) == []


async def test_missing_file_path_returns_none(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["nopath"] = {"file_id": "nopath"}

    result = await download_telegram_media(api, "nopath")

    assert result is None


async def test_reported_size_over_cap_skips_download(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["huge"] = {
        "file_id": "huge", "file_path": "video/huge.mp4", "file_size": MAX_DOWNLOAD_BYTES + 1,
    }

    result = await download_telegram_media(api, "huge")

    assert result is None
    assert api.download_calls == []  # mai chiamato: lo skip è pre-download


async def test_download_failure_returns_none(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["broken"] = {"file_id": "broken", "file_path": "video/broken.mp4"}
    api.download_errors["video/broken.mp4"] = TelegramAPIError(
        413, "file exceeds cap: video/broken.mp4"
    )

    result = await download_telegram_media(api, "broken")

    assert result is None


async def test_custom_max_bytes_is_forwarded(tmp_path, monkeypatch) -> None:
    import jenny.channels.telegram_media as mod

    monkeypatch.setattr(mod, "_telegram_media_dir", lambda: tmp_path)
    api = FakeAPI()
    api.file_info["small"] = {"file_id": "small", "file_path": "documents/small.txt"}
    api.file_bytes["documents/small.txt"] = b"hi"

    await download_telegram_media(api, "small", max_bytes=1024)

    assert api.download_calls == [("documents/small.txt", 1024)]


def test_media_dir_falls_back_to_tempdir_without_workspace() -> None:
    from jenny.channels.telegram_media import _telegram_media_dir
    from jenny.runtime.context import get_runtime_context

    previous = get_runtime_context().workspace_dir
    get_runtime_context().workspace_dir = None
    try:
        result = _telegram_media_dir()
    finally:
        get_runtime_context().workspace_dir = previous

    assert result.is_dir()
    assert result.name == "jenny-telegram-media"
