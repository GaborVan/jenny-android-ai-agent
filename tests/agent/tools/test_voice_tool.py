"""Test per il tool ``transcribe_audio`` (vocale del workspace → testo).

Il tool non contiene logica di riconoscimento: risolve il path con la policy del
workspace, rifiuta ciò che non è audio, e inoltra al livello runtime traducendo
il suo ``None`` in ``stt_unavailable``. La parte bridge si prova in
``tests/runtime/test_stt.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jenny.runtime.stt as runtime_stt
from jenny.agent.tools.voice import TOOLS, TranscribeAudioTool

_ANY_ANDROID: Any = object()


def _ctx(
    *, android: Any = _ANY_ANDROID, enable: bool = True, workspace: Any = None
) -> Any:
    config = SimpleNamespace(
        voice=SimpleNamespace(enable=enable, prefer_offline=False)
    )
    return SimpleNamespace(android_context=android, config=config, workspace=workspace)


# ── Registrazione ──────────────────────────────────────────────────────────


def test_the_module_declares_exactly_one_voice_input_tool() -> None:
    assert {cls.name for cls in TOOLS} == {"transcribe_audio"}


def test_the_tool_points_at_the_voice_config() -> None:
    assert TranscribeAudioTool.config_key == "voice"
    assert TranscribeAudioTool.config_cls().__name__ == "VoiceConfig"


def test_the_tool_is_reachable_from_subagents() -> None:
    assert "subagent" in TranscribeAudioTool._scopes


def test_parameters_serialise_as_json() -> None:
    payload = json.loads(json.dumps(TranscribeAudioTool.parameters.fget(TranscribeAudioTool)))
    assert set(payload["properties"]) == {"path", "language"}
    assert payload["required"] == ["path"]


def test_it_is_not_declared_read_only() -> None:
    # Non scrive nel workspace, ma manda l'audio a un motore esterno: leggerlo
    # come "sola lettura" lo farebbe girare dentro i turni read-only.
    assert TranscribeAudioTool(config=SimpleNamespace()).read_only is False


# ── Attivazione ────────────────────────────────────────────────────────────


def test_enabled_requires_an_android_context() -> None:
    assert TranscribeAudioTool.enabled(_ctx(android=None)) is False
    assert TranscribeAudioTool.enabled(_ctx()) is True


def test_enabled_follows_the_user_toggle() -> None:
    assert TranscribeAudioTool.enabled(_ctx(enable=False)) is False


def test_enabled_is_false_when_the_config_section_is_missing() -> None:
    ctx = SimpleNamespace(android_context=_ANY_ANDROID, config=SimpleNamespace())
    assert TranscribeAudioTool.enabled(ctx) is False


# ── execute ────────────────────────────────────────────────────────────────


def _tool(tmp_path: Path) -> TranscribeAudioTool:
    return TranscribeAudioTool(config=SimpleNamespace(prefer_offline=False), workspace=tmp_path)


@pytest.mark.asyncio
async def test_a_workspace_audio_file_is_forwarded_to_the_runtime(
    monkeypatch: Any, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, bool]] = []

    async def _fake_transcribe(path: str, *, language: str = "", prefer_offline: bool = False):
        seen.append((path, language, prefer_offline))
        return {"ok": True, "text": "привіт"}

    monkeypatch.setattr(runtime_stt, "transcribe_file", _fake_transcribe)
    (tmp_path / "voice.ogg").write_bytes(b"x")

    payload = json.loads(await _tool(tmp_path).execute(path="voice.ogg", language="uk"))

    assert payload == {"ok": True, "text": "привіт"}
    assert seen == [(str(tmp_path / "voice.ogg"), "uk", False)]


@pytest.mark.asyncio
async def test_a_missing_file_is_reported(monkeypatch: Any, tmp_path: Path) -> None:
    async def _never(*args: Any, **kwargs: Any):  # pragma: no cover - non deve girare
        raise AssertionError("the runtime must not be reached")

    monkeypatch.setattr(runtime_stt, "transcribe_file", _never)

    payload = json.loads(await _tool(tmp_path).execute(path="nope.ogg"))

    assert payload["ok"] is False
    assert payload["error"] == "file_not_found"


@pytest.mark.asyncio
async def test_a_non_audio_file_is_refused(monkeypatch: Any, tmp_path: Path) -> None:
    async def _never(*args: Any, **kwargs: Any):  # pragma: no cover - non deve girare
        raise AssertionError("the runtime must not be reached")

    monkeypatch.setattr(runtime_stt, "transcribe_file", _never)
    (tmp_path / "notes.txt").write_text("ciao", encoding="utf-8")

    payload = json.loads(await _tool(tmp_path).execute(path="notes.txt"))

    assert payload["ok"] is False
    assert payload["error"] == "unsupported_audio"


@pytest.mark.asyncio
async def test_outside_android_the_runtime_none_becomes_stt_unavailable(
    monkeypatch: Any, tmp_path: Path
) -> None:
    async def _fake_transcribe(path: str, *, language: str = "", prefer_offline: bool = False):
        return None

    monkeypatch.setattr(runtime_stt, "transcribe_file", _fake_transcribe)
    (tmp_path / "voice.ogg").write_bytes(b"x")

    payload = json.loads(await _tool(tmp_path).execute(path="voice.ogg"))

    assert payload["ok"] is False
    assert payload["error"] == "stt_unavailable"


@pytest.mark.asyncio
async def test_the_offline_preference_travels_from_the_config(
    monkeypatch: Any, tmp_path: Path
) -> None:
    seen: list[bool] = []

    async def _fake_transcribe(path: str, *, language: str = "", prefer_offline: bool = False):
        seen.append(prefer_offline)
        return {"ok": True, "text": "ok"}

    monkeypatch.setattr(runtime_stt, "transcribe_file", _fake_transcribe)
    (tmp_path / "voice.ogg").write_bytes(b"x")
    tool = TranscribeAudioTool(
        config=SimpleNamespace(prefer_offline=True), workspace=tmp_path
    )

    await tool.execute(path="voice.ogg")

    assert seen == [True]


@pytest.mark.asyncio
async def test_an_engine_error_is_passed_through_untouched(
    monkeypatch: Any, tmp_path: Path
) -> None:
    async def _fake_transcribe(path: str, *, language: str = "", prefer_offline: bool = False):
        return {
            "ok": False,
            "error": "stt_file_source_unsupported",
            "hint": "needs Android 13",
        }

    monkeypatch.setattr(runtime_stt, "transcribe_file", _fake_transcribe)
    (tmp_path / "voice.ogg").write_bytes(b"x")

    payload = json.loads(await _tool(tmp_path).execute(path="voice.ogg"))

    assert payload == {
        "ok": False,
        "error": "stt_file_source_unsupported",
        "hint": "needs Android 13",
    }
