"""Test per la localizzazione dei media remoti in WebUIMediaGateway."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenny.webui import media_gateway
from jenny.webui.media_gateway import WebUIMediaGateway


def _media_dir(root: Path):
    def _provider(channel: str | None = None) -> Path:
        target = root if channel is None else root / channel
        target.mkdir(parents=True, exist_ok=True)
        return target

    return _provider


def _gateway(tmp_path: Path) -> WebUIMediaGateway:
    return WebUIMediaGateway(
        workspace_path=tmp_path,
        logger=MagicMock(),
        media_dir=_media_dir(tmp_path / "media"),
        secret=b"unit-test-secret",
    )


@pytest.fixture()
def fake_ingest(monkeypatch, tmp_path):
    """Sostituisce l'ingest reale: scrive un file locale e ne ritorna il path.

    Registra gli URL richiesti così i test possono asserire dedup e selettività.
    """
    calls: list[str] = []
    remote = tmp_path / "media" / "remote"

    async def _fake(url, *, media_dir, logger, client=None):
        calls.append(url)
        if "notimage" in url:
            return None  # simula un URL non ingeribile (non-immagine / bloccato)
        remote.mkdir(parents=True, exist_ok=True)
        target = remote / (str(len(calls)) + ".png")
        target.write_bytes(b"\x89PNG\r\n\x1a\n")
        return target

    monkeypatch.setattr(media_gateway, "ingest_remote_image", _fake)
    return calls


class TestLocalizeTextImages:
    async def test_remote_markdown_rewritten_to_local_path(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        text = "ecco: ![kuma](https://host.test/kumamon.png) fine"
        new_text, _ = await gw.localize_remote_media(text, [])
        assert "https://host.test" not in new_text
        assert str(tmp_path / "media" / "remote") in new_text
        assert fake_ingest == ["https://host.test/kumamon.png"]

    async def test_local_only_text_untouched_no_ingest(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        text = "locale: ![x](./foo.png)"
        new_text, _ = await gw.localize_remote_media(text, [])
        assert new_text == text
        assert fake_ingest == []

    async def test_failed_ingest_leaves_url_untouched(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        text = "![p](https://host.test/notimage.png)"
        new_text, _ = await gw.localize_remote_media(text, [])
        assert new_text == text
        assert fake_ingest == ["https://host.test/notimage.png"]

    async def test_duplicate_url_ingested_once(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        url = "https://host.test/a.png"
        text = f"![1]({url}) e ancora ![2]({url})"
        new_text, _ = await gw.localize_remote_media(text, [])
        assert fake_ingest == [url]
        assert new_text.count("https://host.test") == 0

    async def test_no_images_returns_input(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        text = "nessuna immagine qui"
        new_text, _ = await gw.localize_remote_media(text, [])
        assert new_text == text
        assert fake_ingest == []


class TestLocalizeMediaList:
    async def test_http_entry_localized_others_kept(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        media = ["https://host.test/b.png", "/abs/local/file.png"]
        _, new_media = await gw.localize_remote_media("", media)
        assert new_media[0].endswith(".png")
        assert str(tmp_path / "media" / "remote") in new_media[0]
        assert new_media[1] == "/abs/local/file.png"

    async def test_unchanged_list_returned_as_is(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        media = ["/only/local.png"]
        _, new_media = await gw.localize_remote_media("", media)
        assert new_media == media
        assert fake_ingest == []

    async def test_failed_ingest_keeps_original_url(self, tmp_path, fake_ingest):
        gw = _gateway(tmp_path)
        media = ["https://host.test/notimage.bin"]
        _, new_media = await gw.localize_remote_media("", media)
        assert new_media == media
