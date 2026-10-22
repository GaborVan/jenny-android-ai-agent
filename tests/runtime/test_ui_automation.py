"""Test per ``jenny/runtime/ui_automation.py`` e i relativi tool.

Fuori da Android (nessun android context) tutto deve degradare a no-op senza
sollevare: i tool non si registrano (``enabled()`` False) e le funzioni runtime
ritornano ``None``. Con un context finto e il bridge finto montato sul seam
``_resolve_bridge_class``, i risultati del bridge passano parsati ai tool.
"""

from __future__ import annotations

import pytest

import jenny.agent.tools.ui_automation as tools_module
import jenny.runtime.ui_automation as ui_automation


class _FakeConfig:
    """Config minimale per ``enabled()``/``create()`` (toggle ON)."""

    class _Ui:
        enable = True

    def __init__(self) -> None:
        self.ui_automation = self._Ui()


class _FakeCtx:
    def __init__(self, android_context: object | None) -> None:
        self.android_context = android_context
        self.config = _FakeConfig()


# ── Runtime: fuori da Android tutto None, nessuna eccezione ───────────────

@pytest.mark.asyncio
async def test_runtime_returns_none_without_android_context(monkeypatch):
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: None)
    assert await ui_automation.ui_status() is None
    assert await ui_automation.screen_dump() is None
    assert await ui_automation.screenshot() is None
    assert await ui_automation.tap(10, 20) is None
    assert await ui_automation.tap_by_text("x") is None
    assert await ui_automation.swipe(0, 0, 100, 100, 300) is None
    assert await ui_automation.type_text("hello") is None
    assert await ui_automation.press_global("back") is None
    assert await ui_automation.open_accessibility_settings() is None


@pytest.mark.asyncio
async def test_runtime_unknown_global_key_rejected(monkeypatch):
    # Whitelist: chiavi ignote rifiutate prima ancora di toccare il bridge.
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: object())
    result = await ui_automation.press_global("delete_everything")
    assert result is not None
    assert result["ok"] is False
    assert "unknown_key" in result["error"]


@pytest.mark.asyncio
async def test_runtime_parses_bridge_json(monkeypatch):
    """Con bridge finto, il JSON del bridge arriva parsato al chiamante."""
    context = object()

    class _FakeBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def tap(self, x: int, y: int) -> str:
            return '{"ok":true}'

        def screenDump(self) -> str:  # noqa: N802
            return '{"ok":true,"package":"com.example","nodes":[]}'

        def status(self) -> str:
            return '{"ok":true,"connected":true,"package":"com.example"}'

        def captureScreenshot(self, path: str) -> str:  # noqa: N802
            return '{"ok":true,"path":"' + path + '","width":800,"height":600}'

    monkeypatch.setattr(ui_automation, "get_android_context", lambda: context)
    monkeypatch.setattr(
        ui_automation, "_resolve_bridge_class", lambda: _FakeBridge
    )

    status = await ui_automation.ui_status()
    assert status is not None and status["connected"] is True

    dump = await ui_automation.screen_dump()
    assert dump is not None and dump["package"] == "com.example"

    shot = await ui_automation.screenshot()
    assert shot is not None and shot["ok"] is True and shot["width"] == 800

    tapped = await ui_automation.tap(5, 5)
    assert tapped is not None and tapped["ok"] is True


# ── Tools: enabled() rispecchia android context + toggle ──────────────────

def test_tools_disabled_without_android_context():
    for tool_cls in tools_module.TOOLS:
        assert tool_cls.enabled(_FakeCtx(android_context=None)) is False


def test_tools_enabled_with_android_context_and_toggle():
    for tool_cls in tools_module.TOOLS:
        assert tool_cls.enabled(_FakeCtx(android_context=object())) is True


def test_tools_disabled_when_toggle_off():
    class _OffConfig(_FakeConfig):
        class _Ui:
            enable = False

        def __init__(self) -> None:
            self.ui_automation = self._Ui()

    class _Ctx:
        android_context = object()
        config = _OffConfig()

    for tool_cls in tools_module.TOOLS:
        assert tool_cls.enabled(_Ctx()) is False


def test_tool_names_registered():
    names = {t.name for t in tools_module.TOOLS}
    assert names == {
        "ui_status",
        "ui_screen_dump",
        "ui_screenshot",
        "ui_tap",
        "ui_swipe",
        "ui_type",
        "ui_press",
        "ui_open_accessibility_settings",
    }


# ── Tool esecuzione: errore uniforme quando il runtime degrada ────────────

@pytest.mark.asyncio
async def test_execute_returns_unavailable_error(monkeypatch):
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: None)
    tool = tools_module.UiStatusTool(config=_FakeConfig().ui_automation)
    out = await tool.execute()
    assert '"error"' in out and "ui_automation_unavailable" in out


async def test_accessibility_enabled_without_android_context(monkeypatch):
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: None)
    assert await ui_automation.accessibility_enabled() is None


async def test_accessibility_enabled_reports_the_bridge(monkeypatch):
    monkeypatch.setattr(
        ui_automation, "_BRIDGE", ui_automation.BridgeCache("com.flagdizero.jenny.UiAutomationBridge")
    )

    class _FakeBridge:
        enabled = True

        def __init__(self, context):
            pass

        def isEnabled(self):  # noqa: N802
            return self.enabled

    monkeypatch.setattr(ui_automation, "_resolve_bridge_class", lambda: _FakeBridge)
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: object())
    assert await ui_automation.accessibility_enabled() is True
    _FakeBridge.enabled = False
    assert await ui_automation.accessibility_enabled() is False


@pytest.mark.parametrize("target", [{"index": 0}, {"node_id": "field"}])
async def test_tap_cached_target(monkeypatch, target):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(ui_automation, "_LAST_NODES", [{"id": "field", "bounds": [10, 20, 31, 61]}])
    tap = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ui_automation, "tap", tap)
    assert await ui_automation.tap_target(**target) == {"ok": True}
    tap.assert_awaited_once_with(20, 40)


@pytest.mark.parametrize("nodes,target,error", [
    ([], {"index": 0}, "no_screen_dump"),
    ([{"id": "field"}], {"index": -1}, "element_not_found"),
    ([{"id": "field"}], {"index": 1}, "element_not_found"),
    ([{"id": "field"}], {"node_id": "FIELD"}, "element_not_found"),
    ([{"id": "field"}], {"index": 0}, "element_not_found"),
])
async def test_tap_target_errors(monkeypatch, nodes, target, error):
    monkeypatch.setattr(ui_automation, "_LAST_NODES", nodes)
    assert (await ui_automation.tap_target(**target))["error"] == error


@pytest.mark.parametrize("bounds", [None, [], [1, 2], "1234", [0, 0, 0, 10],
                                        [2, 0, 1, 10], [0, 0, True, 10], [0, 0, float("nan"), 10]])
def test_malformed_node_bounds(bounds):
    assert ui_automation._node_center({"bounds": bounds}) is None


async def test_target_off_android(monkeypatch):
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: None)
    monkeypatch.setattr(ui_automation, "_LAST_NODES", [{"bounds": [0, 0, 10, 10]}])
    assert await ui_automation.tap_target(index=0) is None
    assert await ui_automation.tap_target(x=1, y=2) is None
    assert await ui_automation.type_text_into("hi", index=0) is None


@pytest.mark.parametrize("target", [{}, {"index": 0}, {"node_id": "field"}])
async def test_type_target_focus_order(monkeypatch, target):
    from unittest.mock import AsyncMock, Mock, call

    calls = Mock()
    calls.attach_mock(AsyncMock(return_value={"ok": True}), "tap")
    calls.attach_mock(AsyncMock(), "sleep")
    calls.attach_mock(AsyncMock(return_value={"ok": True}), "type")
    monkeypatch.setattr(ui_automation, "tap_target", calls.tap)
    monkeypatch.setattr(ui_automation.asyncio, "sleep", calls.sleep)
    monkeypatch.setattr(ui_automation, "type_text", calls.type)
    assert await ui_automation.type_text_into("hi", **target) == {"ok": True}
    expected = []
    if target:
        expected = [call.tap(index=target.get("index"), node_id=target.get("node_id")), call.sleep(0.3)]
    assert calls.mock_calls == expected + [call.type("hi")]


@pytest.mark.parametrize("result", [None, {"ok": False, "error": "service_not_enabled"}])
async def test_type_target_stops_after_failed_tap(monkeypatch, result):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(ui_automation, "tap_target", AsyncMock(return_value=result))
    type_mock = AsyncMock()
    monkeypatch.setattr(ui_automation, "type_text", type_mock)
    assert await ui_automation.type_text_into("hi", index=0) == result
    type_mock.assert_not_awaited()


async def test_dump_cache_refresh_and_reset(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    bridge = Mock()
    bridge.screenDump.side_effect = [
        '{"ok":true,"nodes":[{"id":"first"}]}',
        '{"ok":true,"nodes":[{"id":"second"}]}',
        '{"ok":false,"error":"service_not_enabled"}',
    ]
    monkeypatch.setattr(ui_automation, "get_android_context", lambda: object())
    monkeypatch.setattr(ui_automation, "_get_bridge", AsyncMock(return_value=bridge))
    ui_automation.reset_ui_automation_state()
    for name in ("first", "second"):
        result = await ui_automation.screen_dump()
        assert ui_automation.last_dump_nodes() == result["nodes"] == [{"id": name}]
        copy = ui_automation.last_dump_nodes()
        copy.clear()
        assert ui_automation.last_dump_nodes()
    await ui_automation.screen_dump()
    assert ui_automation.last_dump_nodes() == [{"id": "second"}]
    ui_automation.reset_ui_automation_state()
    assert ui_automation.last_dump_nodes() == []
