"""Tool per trascrivere in testo un audio già presente nel workspace (solo Android).

Serve ai file audio che sono **già dentro** il workspace: un vocale salvato nei
download, un'audio-nota caricata dalla WebUI, un allegato arrivato da un altro
canale. Il vocale in arrivo da Telegram non passa da qui — lo trascrive il
canale mentre lo scarica (``channels/telegram.py``), perché lì il file non è
ancora nominabile dal modello.

Il path è vincolato dalla stessa policy dei tool di filesystem
(``resolve_workspace_path``): il modello non può far leggere l'audio di un file
arbitrario del dispositivo, solo file che stanno dove gli è permesso leggere.

La trascrizione usa il motore di riconoscimento di sistema: nessuna chiave,
nessun servizio nostro. Chi ascolta davvero è quel motore — v.
``config.voice.prefer_offline`` e la pagina Privacy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.path_utils import resolve_workspace_path
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.config.tool_schemas import VoiceConfig
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_tool_workspace,
    current_turn_is_readonly,
)
from jenny.security.workspace_policy import _safe_expanduser

# Estensioni che vale la pena porgere al decodificatore. Non è una whitelist di
# sicurezza — il confine è il path — ma di senso: un .txt non è audio, e dirlo
# subito costa meno che farlo scoprire al decoder.
_AUDIO_SUFFIXES = frozenset(
    {
        ".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav", ".aac",
        ".flac", ".amr", ".3gp", ".mp4", ".webm", ".caf", ".wma",
    }
)


def _android_enabled(ctx: Any) -> bool:
    return (
        bool(getattr(ctx, "android_context", None))
        and getattr(ctx.config, "voice", None) is not None
        and ctx.config.voice.enable
    )


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema(
            "Audio file to transcribe, relative to the workspace (or inside the "
            "media folder). Any format the phone can decode: ogg/opus, m4a, mp3, "
            "wav, amr, and the audio track of a video."
        ),
        language=StringSchema(
            "Optional BCP-47 language tag (e.g. 'uk-UA'). Empty or omitted lets "
            "the device's speech service pick the language."
        ),
        required=["path"],
    )
)
class TranscribeAudioTool(Tool):
    """Trascrive in testo un file audio del workspace (motore di sistema)."""

    _scopes = {"core", "orchestrator", "subagent"}

    name: str = "transcribe_audio"
    description: str = (
        "Transcribe an audio file that is already in the workspace to text, "
        "using the phone's own speech recognition engine. Use it for a voice "
        "note or recording the user points you at, not for text documents. It "
        "reads a file: to dictate live, the user taps the microphone in the "
        "app. If the device has no usable speech service, or runs Android below "
        "13, the error is not transient — say so in text instead of retrying."
    )

    config_key = "voice"

    @classmethod
    def config_cls(cls):
        return VoiceConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return _android_enabled(ctx)

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=ctx.config.voice, workspace=getattr(ctx, "workspace", None))

    def __init__(self, config: Any = None, workspace: Any = None) -> None:
        self.config = config
        self._workspace = _safe_expanduser(workspace) if workspace is not None else None

    @property
    def read_only(self) -> bool:
        # Non scrive nel workspace... ma manda l'audio a un motore di
        # riconoscimento, che può stare fuori dal telefono: è un effetto sul
        # mondo, non una lettura. Stessa classificazione di ``speak``.
        return False

    def _resolve(self, path: str) -> Path:
        access = current_tool_workspace(self._workspace, restrict_to_workspace=False)
        workspace = access.project_path or self._workspace
        if not access.restrict_to_workspace:
            candidate = _safe_expanduser(path)
            if candidate.is_absolute() or workspace is None:
                return candidate
            return Path(workspace) / candidate
        if workspace is None:
            raise ValueError("no workspace to resolve a relative path against")
        return resolve_workspace_path(path, workspace, access.allowed_root)

    async def execute(self, path: str, language: str = "", **kwargs: Any) -> str:
        from jenny.runtime.stt import transcribe_file

        # Un turno in sola lettura non manda audio a un motore di
        # riconoscimento: non scrive nel workspace, ma esce dal telefono.
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL
        try:
            target = self._resolve(path)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {"ok": False, "error": "path_not_allowed", "hint": str(exc)},
                ensure_ascii=False,
            )
        if not target.is_file():
            return json.dumps(
                {"ok": False, "error": "file_not_found", "hint": f"No file at {path}"},
                ensure_ascii=False,
            )
        if target.suffix.lower() not in _AUDIO_SUFFIXES:
            return json.dumps(
                {
                    "ok": False,
                    "error": "unsupported_audio",
                    "hint": f"{target.name} does not look like an audio file.",
                },
                ensure_ascii=False,
            )
        result = await transcribe_file(
            str(target),
            language=language,
            prefer_offline=bool(getattr(self.config, "prefer_offline", False)),
        )
        if result is None:
            return json.dumps(
                {
                    "ok": False,
                    "error": "stt_unavailable",
                    "hint": "Speech recognition runs on the phone; this host has no engine.",
                },
                ensure_ascii=False,
            )
        return json.dumps(result, ensure_ascii=False)


TOOLS = [TranscribeAudioTool]
