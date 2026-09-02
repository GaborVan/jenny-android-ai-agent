"""Tools per gli appunti di sistema Android (solo Android).

Si registrano solo quando c'è un Android Context e il toggle
``tools.clipboard.enable`` è ON. La logica (bridge nativo ``ClipboardBridge``)
vive in ``jenny/runtime/clipboard.py``.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.config.tool_schemas import ClipboardConfig


def _android_enabled(ctx: Any) -> bool:
    return (
        bool(getattr(ctx, "android_context", None))
        and getattr(ctx.config, "clipboard", None) is not None
        and ctx.config.clipboard.enable
    )


async def _run(coro: Any, *, error_label: str) -> str:
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{error_label}: {exc}"}, ensure_ascii=False)
    if result is None:
        return json.dumps({"ok": False, "error": "clipboard_unavailable"}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@tool_parameters(tool_parameters_schema(required=[]))
class ClipboardGetTool(Tool):
    """Legge il testo corrente dagli appunti di sistema."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "clipboard_get"
    description = (
        "Read the current system clipboard text. Useful to grab a code or link "
        "the user copied in another app, or to verify what ui_type/clipboard_set "
        "just placed. Note: Android 10+ only lets apps read the clipboard while "
        "focused or as the default IME — if blocked, ask the user to open Jenny "
        "and retry."
    )

    config_key = "clipboard"

    @classmethod
    def config_cls(cls):
        return ClipboardConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.clipboard)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.clipboard import clipboard_get

        return await _run(clipboard_get(), error_label="clipboard_get")


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema("Text to place on the system clipboard."),
        required=["text"],
    )
)
class ClipboardSetTool(Tool):
    """Scrive testo negli appunti di sistema (per incollarlo altrove)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name = "clipboard_set"
    description = (
        "Write text to the system clipboard. Use before ui_tap/ui_type to paste "
        "content into another app's field (tap the field, then trigger paste), "
        "or to hand the user something to paste elsewhere."
    )

    config_key = "clipboard"

    @classmethod
    def config_cls(cls):
        return ClipboardConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.clipboard)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, text: str, **kwargs: Any) -> str:
        from jenny.runtime.clipboard import clipboard_set

        return await _run(clipboard_set(text), error_label="clipboard_set")


TOOLS = [ClipboardGetTool, ClipboardSetTool]
