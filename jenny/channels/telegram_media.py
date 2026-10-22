"""Download di allegati inbound da Telegram nel media store locale.

Convenzione ``InboundMessage.media``: path locali sul filesystem, la stessa
forma già usata dal canale WebSocket — ``prepare_attachments``/``is_image_file``
in ``jenny/utils/document.py`` leggono direttamente dal path, non fanno un
fetch HTTP. Il download passa quindi da qui: ``getFile`` risolve il
``file_id`` Telegram in un ``file_path`` scaricabile, poi lo streaming lo
scrive sotto la media dir runtime, namespace ``telegram`` — stesso schema di
``jenny.webui.media_ingest.ingest_remote_image`` per il namespace ``remote``.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir

from loguru import logger

from jenny.channels.telegram_api import TelegramAPI, TelegramAPIError
from jenny.utils.helpers import ensure_dir
from jenny.utils.path import atomic_write

# Cap prudente sul download: un documento/video senza limite esaurirebbe lo
# storage di un device Android. L'ordine di grandezza è quello dei media in
# uscita (vedi ``telegram._TG_MEDIA_MAX_BYTES``), un po' più permissivo perché
# qui copriamo anche video/documenti oltre alle foto.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def _telegram_media_dir() -> Path:
    """Cartella cache per i media Telegram scaricati.

    Preferisce la media dir runtime (namespace ``telegram``, come
    ``media_ingest`` fa per ``remote``). Il fallback alla tmp dir di sistema
    copre solo il caso in cui il workspace non sia ancora configurato — in
    produzione non accade: ``android_entry`` chiama ``set_workspace_dir``
    prima che il canale Telegram riceva il primo update.
    """
    try:
        from jenny.config.paths import get_media_dir

        return get_media_dir("telegram")
    except RuntimeError:
        return ensure_dir(Path(gettempdir()) / "jenny-telegram-media")


async def download_telegram_media(
    api: TelegramAPI,
    file_id: str,
    *,
    suggested_name: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path | None:
    """Risolve *file_id* e lo scarica nella media dir locale.

    Ritorna ``None`` (loggando) se ``getFile`` fallisce, il file dichiarato
    supera *max_bytes*, o il download si interrompe: degradazione morbida,
    stesso pattern di ``ingest_remote_image`` — mai un'eccezione che abbatta
    la lavorazione dell'update.
    """
    try:
        info = await api.get_file(file_id)
    except TelegramAPIError as e:
        logger.warning("Telegram: getFile failed for {}: {}", file_id, e)
        return None
    file_path = info.get("file_path") if isinstance(info, dict) else None
    if not file_path:
        logger.warning("Telegram: getFile returned no file_path for {}", file_id)
        return None
    reported_size = info.get("file_size") if isinstance(info, dict) else None
    if isinstance(reported_size, int) and reported_size > max_bytes:
        logger.warning(
            "Telegram: media {} too large ({} bytes), skipping download",
            file_id, reported_size,
        )
        return None

    try:
        data = await api.download_file(file_path, max_bytes=max_bytes)
    except TelegramAPIError as e:
        logger.warning("Telegram: download failed for {}: {}", file_id, e)
        return None

    ext = Path(suggested_name or "").suffix or Path(file_path).suffix
    dest = _telegram_media_dir() / f"{file_id}{ext}"
    try:
        atomic_write(dest, data)
    except OSError as e:
        logger.warning("Telegram: failed to persist media {}: {}", file_id, e)
        return None
    return dest
