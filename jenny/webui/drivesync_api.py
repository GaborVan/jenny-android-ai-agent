"""Helper REST per la cloud sync (Google Drive) dalla WebUI.

Stesso confine di ``telegram_api``: questo modulo possiede la forma dei
payload; l'algoritmo e l'I/O restano in ``jenny.runtime.drive_sync``.
"""

from __future__ import annotations

from typing import Any

from jenny.config import store
from jenny.config.loader import load_config
from jenny.config.paths import get_workspace_path
from jenny.config.schema import Config
from jenny.runtime.drive_sync import run_sync, sync_status


async def drive_sync_status_payload() -> dict[str, Any]:
    status = await sync_status(get_workspace_path())
    return {"ok": True, "enabled": load_config().drive_sync.enabled, **status}


async def run_drive_sync_now() -> dict[str, Any]:
    result = await run_sync(get_workspace_path())
    return result


async def update_drive_sync_settings(enabled: bool) -> dict[str, Any]:
    def _apply(config: Config) -> None:
        config.drive_sync.enabled = enabled

    await store.mutate(_apply)
    return await drive_sync_status_payload()
