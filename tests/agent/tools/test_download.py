"""Test per il tool download_file (download universale in workspace/downloads/)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jenny.agent.tools import download as download_mod
from jenny.agent.tools.download import (
    DownloadFileTool,
    _filename_from_disposition,
    _filename_from_url,
    _unique_path,
)

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PDF = b"%PDF-1.7 fake body"


def _tool(tmp_path: Path, handler) -> DownloadFileTool:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, follow_redirects=False)
    return DownloadFileTool(workspace=tmp_path, client=client)


@pytest.fixture(autouse=True)
def _allow_all_urls(monkeypatch):
    """Le richieste sono mockate: bypassa la risoluzione DNS del check SSRF."""
    monkeypatch.setattr(download_mod, "validate_url_target", lambda url: (True, None))


class TestHelpers:
    def test_disposition_plain(self):
        assert _filename_from_disposition('attachment; filename="report.pdf"') == "report.pdf"

    def test_disposition_utf8(self):
        got = _filename_from_disposition("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
        assert got == "résumé.pdf"

    def test_disposition_missing(self):
        assert _filename_from_disposition(None) is None
        assert _filename_from_disposition("inline") is None

    def test_url_basename(self):
        assert _filename_from_url("https://x.example/a/b/photo.jpg?w=1") == "photo.jpg"

    def test_url_without_name(self):
        assert _filename_from_url("https://x.example/") is None

    def test_unique_path_appends_counter(self, tmp_path):
        (tmp_path / "f.pdf").touch()
        (tmp_path / "f-1.pdf").touch()
        assert _unique_path(tmp_path, "f.pdf").name == "f-2.pdf"


class TestDownloadFileTool:
    async def test_saves_pdf_into_downloads(self, tmp_path):
        def handler(request):
            return httpx.Response(
                200, content=_PDF, headers={"content-disposition": 'attachment; filename="doc.pdf"'}
            )

        out = await _tool(tmp_path, handler).execute("https://x.example/file")
        assert "Saved downloads/doc.pdf" in out
        saved = tmp_path / "downloads" / "doc.pdf"
        assert saved.read_bytes() == _PDF
        assert "application/pdf" in out

    async def test_image_reports_inline_embed_hint(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=_PNG)

        out = await _tool(tmp_path, handler).execute("https://x.example/cat.png")
        assert "Saved downloads/cat.png" in out
        assert "![" in out  # hint per l'embed markdown inline

    async def test_explicit_filename_wins(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=_PDF)

        out = await _tool(tmp_path, handler).execute(
            "https://x.example/blob", filename="mio.pdf"
        )
        assert "Saved downloads/mio.pdf" in out
        assert (tmp_path / "downloads" / "mio.pdf").is_file()

    async def test_extension_sniffed_for_extensionless_image(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=_PNG)

        out = await _tool(tmp_path, handler).execute("https://x.example/blob")
        assert ".png" in out

    async def test_collision_gets_suffix(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=_PDF)

        tool = _tool(tmp_path, handler)
        await tool.execute("https://x.example/doc.pdf")
        out = await tool.execute("https://x.example/doc.pdf")
        assert "doc-1.pdf" in out

    async def test_follows_redirect(self, tmp_path):
        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/final.pdf"})
            return httpx.Response(200, content=_PDF)

        out = await _tool(tmp_path, handler).execute("https://x.example/start")
        assert "Saved downloads/final.pdf" in out

    async def test_ssrf_blocked_url_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            download_mod, "validate_url_target", lambda url: (False, "private address")
        )

        def handler(request):  # pragma: no cover - mai raggiunto
            raise AssertionError("no request expected")

        out = await _tool(tmp_path, handler).execute("https://169.254.1.1/x")
        assert out.startswith("Error:")
        assert "private address" in out

    async def test_ssrf_checked_on_redirect_hop(self, tmp_path, monkeypatch):
        calls = []

        def fake_validate(url):
            calls.append(url)
            return (False, "blocked hop") if "evil" in url else (True, None)

        monkeypatch.setattr(download_mod, "validate_url_target", fake_validate)

        def handler(request):
            return httpx.Response(302, headers={"location": "https://evil.example/x"})

        out = await _tool(tmp_path, handler).execute("https://x.example/start")
        assert out.startswith("Error:")
        assert len(calls) == 2

    async def test_size_cap_enforced(self, tmp_path, monkeypatch):
        monkeypatch.setattr(download_mod, "MAX_DOWNLOAD_BYTES", 10)

        def handler(request):
            return httpx.Response(200, content=b"x" * 11)

        out = await _tool(tmp_path, handler).execute("https://x.example/big.bin")
        assert out.startswith("Error:")
        assert "limit" in out

    async def test_http_error_status_is_error(self, tmp_path):
        def handler(request):
            return httpx.Response(404)

        out = await _tool(tmp_path, handler).execute("https://x.example/missing.pdf")
        assert out.startswith("Error:")
        assert "404" in out

    async def test_empty_body_is_error(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=b"")

        out = await _tool(tmp_path, handler).execute("https://x.example/empty.bin")
        assert out.startswith("Error:")

    async def test_non_http_scheme_rejected(self, tmp_path):
        def handler(request):  # pragma: no cover - mai raggiunto
            raise AssertionError("no request expected")

        out = await _tool(tmp_path, handler).execute("file:///etc/passwd")
        assert out.startswith("Error:")

    async def test_too_many_redirects(self, tmp_path):
        def handler(request):
            return httpx.Response(302, headers={"location": "/loop"})

        out = await _tool(tmp_path, handler).execute("https://x.example/loop")
        assert out.startswith("Error:")
        assert "redirect" in out
