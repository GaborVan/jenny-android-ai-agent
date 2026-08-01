"""WebUI-facing access to installed Android apps, via the Kotlin bridge.

Mirrors the lazy bridge resolution/caching pattern used by
``jenny.agent.tools.android_web`` for ``AgenticSearchBridge``, but for
``InstalledAppsBridge``. This is UI-only plumbing (not an agent tool): the
user picks an app from the WebUI, the agent is not involved.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from jenny.android_entry import get_android_context

_BRIDGE_LOCK = asyncio.Lock()
_BRIDGE_INSTANCE: Any = None

_LIST_TIMEOUT_S = 15
_LAUNCH_TIMEOUT_S = 10
_UNINSTALL_TIMEOUT_S = 10
_APP_INFO_TIMEOUT_S = 10


def reset_installed_apps_state() -> None:
    """Drop the cached InstalledAppsBridge so a fresh gateway start can't inherit
    a stale bridge from a previous crashed loop.

    Simmetrico a ``android_web.reset_android_web_state``: prima questo bridge
    non veniva mai resettato (asimmetria → possibile bridge stale dopo un
    restart del gateway). Chiamato da ``android_entry.run_gateway``.
    """
    global _BRIDGE_INSTANCE, _BRIDGE_LOCK
    _BRIDGE_LOCK = asyncio.Lock()
    _BRIDGE_INSTANCE = None


def _resolve_bridge_class() -> Any:
    """Resolve the Kotlin InstalledAppsBridge class via Chaquopy."""
    from java import jclass  # only importable under the Chaquopy runtime

    return jclass("com.flagdizero.jenny.InstalledAppsBridge")


async def _get_bridge(context: Any) -> Any:
    """Build or return a cached InstalledAppsBridge instance (thread-safe)."""
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is not None:
        return _BRIDGE_INSTANCE
    async with _BRIDGE_LOCK:
        if _BRIDGE_INSTANCE is not None:
            return _BRIDGE_INSTANCE
        bridge_cls = _resolve_bridge_class()
        try:
            _BRIDGE_INSTANCE = bridge_cls(context)
        except Exception as exc:
            raise RuntimeError(f"Failed to construct InstalledAppsBridge: {exc}") from exc
        return _BRIDGE_INSTANCE


async def webui_android_apps_payload() -> dict[str, Any]:
    """Return installed launchable Android apps as ``{"apps": [...]}``.

    Returns an empty list (rather than raising) when there is no Android
    context (desktop/test runs) or the bridge call fails, so the WebUI tab
    degrades gracefully instead of erroring the whole apps view.
    """
    context = get_android_context()
    if context is None:
        return {"apps": []}
    try:
        bridge = await _get_bridge(context)
        raw = await asyncio.wait_for(
            asyncio.to_thread(bridge.listInstalledApps), timeout=_LIST_TIMEOUT_S
        )
        apps = json.loads(raw)
    except Exception:
        logger.opt(exception=True).warning("Failed to list installed Android apps")
        return {"apps": []}
    return {"apps": apps}


async def launch_android_app(package_name: str) -> bool:
    """Launch an installed app by package name. Returns False on any failure."""
    context = get_android_context()
    if context is None:
        return False
    try:
        bridge = await _get_bridge(context)
        launched = await asyncio.wait_for(
            asyncio.to_thread(bridge.launchApp, package_name), timeout=_LAUNCH_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("Failed to launch Android app {}", package_name)
        return False
    return bool(launched)


async def uninstall_android_app(package_name: str) -> bool:
    """Open the system uninstall dialog for an app. Returns False on any failure."""
    context = get_android_context()
    if context is None:
        return False
    try:
        bridge = await _get_bridge(context)
        ok = await asyncio.wait_for(
            asyncio.to_thread(bridge.uninstallApp, package_name), timeout=_UNINSTALL_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("Failed to uninstall Android app {}", package_name)
        return False
    return bool(ok)


async def open_android_app_info(package_name: str) -> bool:
    """Open the system "App info" screen for an app. Returns False on any failure."""
    context = get_android_context()
    if context is None:
        return False
    try:
        bridge = await _get_bridge(context)
        ok = await asyncio.wait_for(
            asyncio.to_thread(bridge.openAppInfo, package_name), timeout=_APP_INFO_TIMEOUT_S
        )
    except Exception:
        logger.opt(exception=True).warning("Failed to open app info for {}", package_name)
        return False
    return bool(ok)
