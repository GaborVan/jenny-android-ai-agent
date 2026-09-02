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
