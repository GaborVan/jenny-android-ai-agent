"""Tools UI automation Android: occhi e mani sugli altri app.

Si registrano solo quando c'è un Android Context e il toggle
``tools.ui_automation.enable`` è ON. La logica (bridge nativo verso
``UiAutomationService``) vive in ``jenny/runtime/ui_automation.py``; qui c'è
solo l'affaccio LLM.

Confine di fiducia: l'accessibilità di sistema può leggere lo schermo e
simulare gesture su qualunque app. È un permesso che l'utente concede a mano
(Impostazioni → Accessibilità) e il toggle ``enable`` è la seconda serratura
lato agente; le azioni distruttive restano comunque visibili all'utente sullo
schermo. Nessun tool di questo modulo aggira la workspace policy: operano sul
telefono, non sui file.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.tool_schemas import UiAutomationConfig


def _android_enabled(ctx: Any) -> bool:
    """Criterio comune: solo Android + toggle config ON."""
    return (
        bool(getattr(ctx, "android_context", None))
        and getattr(ctx.config, "ui_automation", None) is not None
        and ctx.config.ui_automation.enable
    )


async def _run(coro: Any, *, error_label: str) -> str:
    """Esegue la coroutine runtime e serializza il risultato in JSON.

    ``None`` (fuori Android / toggle off) o fallimenti lato bridge diventano un
    dict d'errore uniforme; il risultato del bridge è già JSON-serializzabile.
    """
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{error_label}: {exc}"}, ensure_ascii=False)
    if result is None:
        return json.dumps(
            {"ok": False, "error": "ui_automation_unavailable"}, ensure_ascii=False
        )
    return json.dumps(result, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(required=[]))
class UiStatusTool(Tool):
    """Report lo stato del servizio di accessibilità e l'app in primo piano."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_status"
    description = (
        "Check whether the Android accessibility service is enabled and which "
        "app is in the foreground. Call this first before any ui_* action: if "
        "the service is not enabled, ask the user to enable Jenny in Android "
        "Settings → Accessibility (the tool response includes that hint)."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import ui_status

        return await _run(ui_status(), error_label="ui_status")


@tool_parameters(tool_parameters_schema(required=[]))
class UiScreenDumpTool(Tool):
    """Legge l'albero di accessibilità della finestra attiva (testi, id, bounds)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_screen_dump"
    description = (
        "Dump the current screen as a JSON tree of accessible nodes: text, "
        "content descriptions, resource ids, classes and pixel bounds. Use this "
        "to SEE another app's screen (fields, buttons, what is tappable) before "
        "acting with ui_tap / ui_swipe / ui_type. Nodes include clickable, "
        "editable, scrollable and focused flags plus bounds [left,top,right,bottom]."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import screen_dump

        return await _run(screen_dump(), error_label="ui_screen_dump")


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema(
            "Text to find on screen (substring, case-insensitive); taps the "
            "matching clickable node, or the center of a non-clickable one.",
            max_length=200,
        ),
        x=IntegerSchema(
            0,
            description="Absolute x coordinate to tap (alternative to text).",
            minimum=0,
            nullable=True,
        ),
        y=IntegerSchema(
            0,
            description="Absolute y coordinate to tap (alternative to text).",
            minimum=0,
            nullable=True,
        ),
        required=[],
    )
)
class UiTapTool(Tool):
    """Tocca un elemento dello schermo: per testo trovato o per coordinate."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_tap"
    description = (
        "Tap an element on the current screen. Pass `text` (substring of the "
        "visible label) to tap by content, or `x`/`y` to tap raw pixel "
        "coordinates (from ui_screen_dump bounds). One of the two is required."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, text: str | None = None, **kwargs: Any) -> str:
        x = kwargs.pop("x", None)
        y = kwargs.pop("y", None)
        from jenny.runtime.ui_automation import tap, tap_by_text

        if text:
            return await _run(tap_by_text(text), error_label="ui_tap")
        if x is not None and y is not None:
            return await _run(tap(int(x), int(y)), error_label="ui_tap")
        return json.dumps(
            {"ok": False, "error": "Provide either text or x/y coordinates."},
            ensure_ascii=False,
        )


@tool_parameters(
    tool_parameters_schema(
        x1=IntegerSchema(0, description="Start x.", minimum=0),
        y1=IntegerSchema(0, description="Start y.", minimum=0),
        x2=IntegerSchema(0, description="End x.", minimum=0),
        y2=IntegerSchema(0, description="End y.", minimum=0),
        durationMs=IntegerSchema(
            300,
            description="Gesture duration in ms (default 300).",
            minimum=1,
            maximum=5000,
        ),
        required=["x1", "y1", "x2", "y2"],
    )
)
class UiSwipeTool(Tool):
    """Esegue uno swipe/scroll da un punto all'altro dello schermo."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_swipe"
    description = (
        "Swipe (scroll/drag) from (x1,y1) to (x2,y2) in pixel coordinates from "
        "ui_screen_dump bounds, over durationMs milliseconds. Vertical swipe "
        "down = scroll up and vice versa."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import swipe

        return await _run(
            swipe(
                int(kwargs["x1"]),
                int(kwargs["y1"]),
                int(kwargs["x2"]),
                int(kwargs["y2"]),
                int(kwargs.get("durationMs", 300)),
            ),
            error_label="ui_swipe",
        )


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema("Text to type into the focused editable field."),
        required=["text"],
    )
)
class UiTypeTextTool(Tool):
    """Scrive testo nel campo editabile focalizzato."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_type"
    description = (
        "Type text into the currently focused editable field of another app "
        "(uses ACTION_SET_TEXT, so it replaces existing content). Tap the field "
        "first with ui_tap if nothing is focused."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, text: str, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import type_text

        return await _run(type_text(text), error_label="ui_type")


@tool_parameters(
    tool_parameters_schema(
        key=StringSchema(
            "Global action: back, home, recents or notifications.",
            enum=["back", "home", "recents", "notifications"],
        ),
        required=["key"],
    )
)
class UiPressTool(Tool):
    """Esegue un'azione globale di sistema (back/home/recents)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_press"
    description = (
        "Trigger a system-level action: back, home, recents or notifications. "
        "Useful to leave another app (back/home) or to open the notifications "
        "shade."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, key: str, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import press_global

        return await _run(press_global(key), error_label="ui_press")


@tool_parameters(tool_parameters_schema(required=[]))
class UiOpenSettingsTool(Tool):
    """Відкриває системні налаштування доступності (щоб увімкнути Jenny)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_open_accessibility_settings"
    description = (
        "Open the Android Accessibility settings so the user can enable Jenny. "
        "Use this when ui_status reports the service is not enabled yet."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import open_accessibility_settings

        return await _run(
            open_accessibility_settings(), error_label="ui_open_accessibility_settings"
        )


@tool_parameters(tool_parameters_schema(required=[]))
class UiScreenshotTool(Tool):
    """Cattura uno screenshot della finestra attiva (PNG nel workspace)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "ui_screenshot"
    description = (
        "Capture a screenshot of the current screen as a PNG saved under "
        "<workspace>/screenshots/. Use this to SEE the screen visually "
        "(icons, layouts, graphics) where ui_screen_dump's text tree is blind. "
        "Requires Android 11+ and the accessibility service enabled."
    )

    config_key = "ui_automation"

    @classmethod
    def config_cls(cls):
        return UiAutomationConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.ui_automation)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.ui_automation import screenshot

        return await _run(screenshot(), error_label="ui_screenshot")


# Registrazione esplicita dei tool di questo modulo (letta da loader.py).
TOOLS = [
    UiStatusTool,
    UiScreenDumpTool,
    UiScreenshotTool,
    UiTapTool,
    UiSwipeTool,
    UiTypeTextTool,
    UiPressTool,
    UiOpenSettingsTool,
]
