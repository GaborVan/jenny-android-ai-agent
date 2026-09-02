"""Test per ``jenny/runtime/notifications.py`` e ``jenny/runtime/clipboard.py``.

Fuori da Android tutto degrada a no-op senza sollevare (``None``); con un
context finto e il bridge finto montato sul seam ``_resolve_bridge_class``, i
risultati JSON del bridge arrivano parsati al chiamante.
"""

from __future__ import annotations

import pytest

import jenny.runtime.clipboard as clipboard
import jenny.runtime.notifications as notifications

# ── Notifications: fuori da Android tutto None ─────────────────────────────

@pytest.mark.asyncio
async def test_notifications_none_without_android_context(monkeypatch):
    monkeypatch.setattr(notifications, "get_android_context", lambda: None)
    assert await notifications.list_notifications() is None
    assert await notifications.dismiss_notification("k") is None
    assert await notifications.open_notification_settings() is None


@pytest.mark.asyncio
async def test_notifications_parses_bridge_json(monkeypatch):
    context = object()

    class _FakeBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def getActiveNotifications(self) -> str:  # noqa: N802
            return '{"ok":true,"notifications":[{"key":"k1","package":"com.x","title":"Code","text":"123456"}]}'

        def dismissNotification(self, key: str) -> str:  # noqa: N802
            return '{"ok":true}'

    monkeypatch.setattr(notifications, "get_android_context", lambda: context)
    monkeypatch.setattr(notifications, "_resolve_bridge_class", lambda: _FakeBridge)

    result = await notifications.list_notifications()
    assert result is not None and result["ok"] is True
    assert result["notifications"][0]["text"] == "123456"

    dismissed = await notifications.dismiss_notification("k1")
    assert dismissed is not None and dismissed["ok"] is True


# ── Clipboard: fuori da Android tutto None ─────────────────────────────────

@pytest.mark.asyncio
async def test_clipboard_none_without_android_context(monkeypatch):
    monkeypatch.setattr(clipboard, "get_android_context", lambda: None)
    assert await clipboard.clipboard_get() is None
    assert await clipboard.clipboard_set("hi") is None


@pytest.mark.asyncio
async def test_clipboard_parses_bridge_json(monkeypatch):
    context = object()

    class _FakeBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def getClipboard(self) -> str:  # noqa: N802
            return '{"ok":true,"text":"hello"}'

        def setClipboard(self, text: str) -> str:  # noqa: N802
            return '{"ok":true}'

    monkeypatch.setattr(clipboard, "get_android_context", lambda: context)
    monkeypatch.setattr(clipboard, "_resolve_bridge_class", lambda: _FakeBridge)

    got = await clipboard.clipboard_get()
    assert got is not None and got["text"] == "hello"

    set_result = await clipboard.clipboard_set("hi")
    assert set_result is not None and set_result["ok"] is True
