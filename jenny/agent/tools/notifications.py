"""Tools per le notifiche di sistema Android (solo Android).

Si registrano solo quando c'è un Android Context e il toggle
``tools.notifications.enable`` è ON. La logica (bridge nativo verso
``NotificationListenerBridge``) vive in ``jenny/runtime/notifications.py``.

Confine di fiducia: le notifiche possono contenere dati personali (codici
2FA inclusi). L'accesso è concesso a mano dall'utente (Impostazioni →
Notifiche → Accesso alle notifiche); i formatter dell'activity stream non
ripetono MAI il contenuto.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.config.tool_schemas import NotificationsConfig


def _android_enabled(ctx: Any) -> bool:
    return (
        bool(getattr(ctx, "android_context", None))
        and getattr(ctx.config, "notifications", None) is not None
        and ctx.config.notifications.enable
    )


async def _run(coro: Any, *, error_label: str) -> str:
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{error_label}: {exc}"}, ensure_ascii=False)
    if result is None:
        return json.dumps({"ok": False, "error": "notifications_unavailable"}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(required=[]))
class ListNotificationsTool(Tool):
    """Legge le notifiche attive (titolo, testo, pacchetto, tempo)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "list_notifications"
    description = (
        "List active Android notifications: package, title, text, post time. "
        "Useful to read a 2FA code, a message or an app status that arrived "
        "without opening the app. Returns [] when the shade is empty. Requires "
        "notification access enabled (see the error hint to enable it)."
    )

    config_key = "notifications"

    @classmethod
    def config_cls(cls):
        return NotificationsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.notifications)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.notifications import list_notifications

        return await _run(list_notifications(), error_label="list_notifications")


@tool_parameters(
    tool_parameters_schema(
        key=StringSchema("Notification key from list_notifications."),
        required=["key"],
    )
)
class DismissNotificationTool(Tool):
    """Rimuove una notifica attiva (per chiave)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "dismiss_notification"
    description = (
        "Dismiss a single active notification by its key (from "
        "list_notifications). Use after acting on it, e.g. after reading a "
        "2FA code, so the shade stays clean."
    )

    config_key = "notifications"

    @classmethod
    def config_cls(cls):
        return NotificationsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.notifications)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, key: str, **kwargs: Any) -> str:
        from jenny.runtime.notifications import dismiss_notification

        return await _run(dismiss_notification(key), error_label="dismiss_notification")


@tool_parameters(tool_parameters_schema(required=[]))
class OpenNotificationSettingsTool(Tool):
    """Apre le Impostazioni di accesso alle notifiche di sistema."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "open_notification_settings"
    description = (
        "Open the Android notification-access settings so the user can enable "
        "Jenny. Use when list_notifications reports the service is not enabled."
    )

    config_key = "notifications"

    @classmethod
    def config_cls(cls):
        return NotificationsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.notifications)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.notifications import open_notification_settings

        return await _run(
            open_notification_settings(), error_label="open_notification_settings"
        )


TOOLS = [
    ListNotificationsTool,
    DismissNotificationTool,
    OpenNotificationSettingsTool,
]
