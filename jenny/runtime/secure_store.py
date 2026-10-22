"""Segreti persistenti protetti dal Keystore Android; degrada senza bridge."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from jenny.runtime.chaquopy_bridge import BridgeCache
from jenny.runtime.context import get_android_context

OPENCLAW_NODE_TOKEN = "openclaw_node_device_token"
_BRIDGE = BridgeCache("com.flagdizero.jenny.SecureStoreBridge")


def reset_secure_store_state() -> None:
    """Azzera la cache a ogni avvio del gateway."""
    _BRIDGE.reset()


def _resolve_bridge_class() -> Any:
    return _BRIDGE.resolve_class()


async def _get_bridge(context: Any) -> Any:
    return await _BRIDGE.get(context, resolve=_resolve_bridge_class)


async def _call(method: str, *args: str) -> dict[str, Any]:
    try:
        context = get_android_context()
        if context is None:
            return {}
        bridge = await _get_bridge(context)
        result = json.loads(str(await asyncio.to_thread(getattr(bridge, method), *args)))
        if isinstance(result, dict) and result.get("ok") is True:
            return result
    except Exception:  # noqa: BLE001 - best effort, senza esporre segreti negli errori
        pass
    logger.debug("SecureStoreBridge operation failed")
    return {}


async def get_secret(name: str) -> str | None:
    value = (await _call("getSecret", name)).get("value")
    return value if isinstance(value, str) else None


async def set_secret(name: str, value: str) -> bool:
    return (await _call("putSecret", name, value)).get("ok") is True


async def remove_secret(name: str) -> bool:
    return (await _call("removeSecret", name)).get("ok") is True
