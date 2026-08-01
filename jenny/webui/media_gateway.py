"""Media gateway services shared by WebUI HTTP routes and WebSocket frames."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from jenny.config.paths import get_media_dir
from jenny.webui.media_api import (
    serve_signed_media,
    sign_media_path,
    sign_or_stage_media_path,
    signed_media_attachments,
)
from jenny.webui.media_ingest import ingest_remote_image
from jenny.webui.transcript import rewrite_local_markdown_images
from jenny.webui.transcript_markdown import MARKDOWN_LOCAL_IMAGE_RE


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def _unwrap_markdown_url(raw: str) -> str:
    url = raw.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1].strip()
    return url


class WebUIMediaGateway:
    """Own media URL signing and WebUI markdown/media augmentation."""

    def __init__(
        self,
        *,
        workspace_path: Path,
        logger: Any,
        media_dir: Callable[[str | None], Path] | None = None,
        secret: bytes | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.logger = logger
        self._media_dir = media_dir or (lambda channel=None: get_media_dir(channel))
        self.secret = secret or secrets.token_bytes(32)

    def serve_signed_media(
        self,
        sig: str,
        payload: str,
        *,
        request: WsRequest | None = None,
    ) -> Response:
        return serve_signed_media(
            sig,
            payload,
            secret=self.secret,
            request=request,
            media_dir=self._media_dir,
        )

    def sign_media_path(self, abs_path: Path) -> str | None:
        return sign_media_path(
            abs_path,
            secret=self.secret,
            media_dir=self._media_dir,
        )

    def sign_or_stage_media_path(self, path: Path) -> dict[str, str] | None:
        return sign_or_stage_media_path(
            path,
            secret=self.secret,
            media_dir=self._media_dir,
            logger=self.logger,
        )

    def rewrite_local_markdown_images(
        self,
        text: str,
        *,
        workspace_path: Path | None = None,
    ) -> str:
        return rewrite_local_markdown_images(
            text,
            workspace_path=workspace_path or self.workspace_path,
            sign_path=self.sign_or_stage_media_path,
        )

    def augment_transcript_media(self, paths: list[str]) -> list[dict[str, Any]]:
        return signed_media_attachments(
            paths,
            sign_path=self.sign_or_stage_media_path,
        )

    async def localize_remote_media(
        self, text: str, media: list[str]
    ) -> tuple[str, list[str]]:
        """Scarica le immagini remote referenziate e le sostituisce con path locali.

        Copre entrambi gli hook: le immagini markdown ``![](http…)`` nel testo e
        gli URL http(s) nella lista ``media``. Ogni URL ingerito con successo
        diventa un file locale sotto la media dir; il resto della pipeline
        (firma al wire, ri-firma al replay) lo tratta come qualsiasi media
        locale. In caso di fallimento l'URL resta invariato (degradazione
        morbida). Ritorna ``(nuovo_testo, nuova_lista_media)``.
        """
        new_text = await self._localize_text_images(text)
        new_media = await self._localize_media_list(media)
        return new_text, new_media

    async def _localize_text_images(self, text: str) -> str:
        if not text or "![" not in text:
            return text
        resolved: dict[str, str] = {}
        for match in MARKDOWN_LOCAL_IMAGE_RE.finditer(text):
            url = _unwrap_markdown_url(match.group(2))
            if not _is_http_url(url) or url in resolved:
                continue
            local = await ingest_remote_image(
                url, media_dir=self._media_dir, logger=self.logger
            )
            if local is not None:
                resolved[url] = str(local)
        if not resolved:
            return text

        def _replace(match: Any) -> str:
            url = _unwrap_markdown_url(match.group(2))
            local = resolved.get(url)
            if not local:
                return match.group(0)
            title = match.group(3) or ""
            return f"![{match.group(1)}]({local}{title})"

        return MARKDOWN_LOCAL_IMAGE_RE.sub(_replace, text)

    async def _localize_media_list(self, media: list[str]) -> list[str]:
        if not media:
            return media
        out: list[str] = []
        changed = False
        for entry in media:
            if _is_http_url(entry):
                local = await ingest_remote_image(
                    entry, media_dir=self._media_dir, logger=self.logger
                )
                if local is not None:
                    out.append(str(local))
                    changed = True
                    continue
            out.append(entry)
        return out if changed else media
