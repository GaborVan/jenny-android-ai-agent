"""Test per jenny/runtime/notifier.py (alert di sistema Android).

Il bridge Chaquopy non esiste nei test desktop: si verifica la derivazione
pura dei campi (``alert_fields``), il no-op senza contesto Android e il
percorso completo con un bridge finto.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jenny.runtime import notifier
from jenny.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.result = True

    def postAlert(self, title: str, body: str, tag: str) -> bool:  # noqa: N802
        self.calls.append((title, body, tag))
        return self.result


def _meta(kind: str, label: str | None = None) -> dict[str, Any]:
    source: dict[str, str] = {"kind": kind}
    if label is not None:
        source["label"] = label
    return {WEBUI_MESSAGE_SOURCE_METADATA_KEY: source}


class TestAlertFields:
    def test_cron_with_label(self):
        title, body, tag = notifier.alert_fields("ricordati il pane", _meta("cron", "spesa"))
        assert title == "Jenny ⏰ spesa"
        assert tag == "cron:spesa"
        assert body == "ricordati il pane"

    def test_cron_without_label(self):
        title, _, tag = notifier.alert_fields("ping", _meta("cron"))
        assert title == "Jenny ⏰ promemoria"
        assert tag == "cron"

    def test_cron_blank_label_falls_back(self):
        title, _, tag = notifier.alert_fields("ping", _meta("cron", "   "))
        assert title == "Jenny ⏰ promemoria"
        assert tag == "cron"

    def test_heartbeat(self):
        title, _, tag = notifier.alert_fields("evento X accaduto", _meta("heartbeat"))
        assert title == "Jenny · monitoraggio"
        assert tag == "heartbeat"

    def test_plain_message_defaults(self):
        for metadata in (None, {}, {"latency_ms": 3}):
            title, _, tag = notifier.alert_fields("ciao", metadata)
            assert title == "Jenny"
            assert tag == "message"

    def test_malformed_source_is_ignored(self):
        title, _, tag = notifier.alert_fields(
            "ciao", {WEBUI_MESSAGE_SOURCE_METADATA_KEY: "cron"}
        )
        assert title == "Jenny"
        assert tag == "message"

    def test_body_collapses_whitespace_and_truncates(self):
        _, body, _ = notifier.alert_fields("riga1\n\n  riga2\t fine", None)
        assert body == "riga1 riga2 fine"
        _, long_body, _ = notifier.alert_fields("x" * 500, None)
        assert len(long_body) == notifier._BODY_MAX_CHARS
        assert long_body.endswith("…")

    def test_empty_body_placeholder(self):
        _, body, _ = notifier.alert_fields("   \n  ", None)
        assert body == "Nuovo messaggio"


class TestPostAlert:
    async def test_noop_without_android_context(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: None)
        assert await notifier.post_alert("ciao", None) is False

    async def test_posts_via_bridge(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        ok = await notifier.post_alert("ricordati il pane", _meta("cron", "spesa"))
        assert ok is True
        assert bridge.calls == [("Jenny ⏰ spesa", "ricordati il pane", "cron:spesa")]

    async def test_bridge_error_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def broken_get_bridge(context: Any) -> Any:
            raise RuntimeError("no chaquopy here")

        monkeypatch.setattr(notifier, "_get_bridge", broken_get_bridge)
        assert await notifier.post_alert("ciao", None) is False


class TestNotifyDelivery:
    async def test_noop_without_android_context(self, monkeypatch):
        monkeypatch.setattr(notifier, "get_android_context", lambda: None)
        notifier.notify_delivery("ciao", None)
        assert not notifier._TASKS

    async def test_schedules_fire_and_forget_task(self, monkeypatch):
        bridge = _FakeBridge()
        monkeypatch.setattr(notifier, "get_android_context", lambda: object())

        async def fake_get_bridge(context: Any) -> Any:
            return bridge

        monkeypatch.setattr(notifier, "_get_bridge", fake_get_bridge)
        notifier.notify_delivery("evento X", _meta("heartbeat"))
        assert notifier._TASKS
        await asyncio.gather(*notifier._TASKS)
        assert bridge.calls == [("Jenny · monitoraggio", "evento X", "heartbeat")]
        assert not notifier._TASKS
