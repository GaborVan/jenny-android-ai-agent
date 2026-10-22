"""Tool per la sintesi vocale di sistema Android (solo Android).

Si registrano solo quando c'è un Android Context e il toggle
``tools.tts.enable`` è ON. La logica (bridge nativo ``TtsBridge``) vive in
``jenny/runtime/tts.py``.
"""

from __future__ import annotations

import json
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import NumberSchema, StringSchema, tool_parameters_schema
from jenny.config.tool_schemas import TtsConfig


def _android_enabled(ctx: Any) -> bool:
    return (
        bool(getattr(ctx, "android_context", None))
        and getattr(ctx.config, "tts", None) is not None
        and ctx.config.tts.enable
    )


async def _run(coro: Any, *, error_label: str) -> str:
    try:
        result = await coro
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{error_label}: {exc}"}, ensure_ascii=False)
    if result is None:
        return json.dumps({"ok": False, "error": "tts_unavailable"}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


@tool_parameters(
    tool_parameters_schema(
        text=StringSchema("Text to speak aloud."),
        language=StringSchema(
            "Optional BCP-47 language tag (e.g. 'uk-UA'). Empty or omitted uses the "
            "device/engine default language."
        ),
        rate=NumberSchema(
            description="Speech rate, 0.5 (slow) to 2.0 (fast). Defaults to 1.0.",
            minimum=0.5,
            maximum=2.0,
        ),
        required=["text"],
    )
)
class SpeakTool(Tool):
    """Fa leggere del testo ad alta voce attraverso il motore TTS di sistema."""

    _scopes = {"core", "orchestrator", "subagent"}

    name: str = "speak"
    description: str = (
        "Speak text aloud through the phone's system text-to-speech engine. Use this "
        "for voice-assistant style interactions where the user expects to hear a "
        "reply, not just read one. If the reply would be long, summarize it first — "
        "this reads the given text verbatim, so pass a short spoken-friendly version "
        "rather than a full written answer. If the engine is unavailable, the error "
        "is not transient: do not retry in a loop, just tell the user in text."
    )

    config_key = "tts"

    @classmethod
    def config_cls(cls):
        return TtsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.tts)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self, text: str, language: str = "", rate: float = 1.0, **kwargs: Any
    ) -> str:
        from jenny.runtime.tts import speak

        return await _run(speak(text, language, rate), error_label="speak")


@tool_parameters(tool_parameters_schema(required=[]))
class StopSpeakingTool(Tool):
    """Interrompe la sintesi vocale in corso."""

    _scopes = {"core", "orchestrator", "subagent"}

    name: str = "stop_speaking"
    description: str = "Stop whatever the system text-to-speech engine is currently speaking."

    config_key = "tts"

    @classmethod
    def config_cls(cls):
        return TtsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.tts)

    def __init__(self, config: Any = None):
        self.config = config

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, **kwargs: Any) -> str:
        from jenny.runtime.tts import stop

        return await _run(stop(), error_label="stop_speaking")


TOOLS = [SpeakTool, StopSpeakingTool]
