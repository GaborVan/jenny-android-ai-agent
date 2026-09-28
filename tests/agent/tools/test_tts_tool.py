"""Test per i tool ``speak``/``stop_speaking`` (sintesi vocale di sistema).

Due cose, e nient'altro: che i tool si registrino con i nomi giusti e che
l'attivazione segua le due condizioni vere (Android context + toggle
``tools.tts.enable``); e che ``execute`` si limiti a inoltrare al livello
runtime, traducendo il suo ``None`` in ``tts_unavailable``. La logica del bridge
si prova in ``tests/runtime/test_tts.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import jenny.runtime.tts as runtime_tts
from jenny.agent.tools.tts import TOOLS, SpeakTool, StopSpeakingTool
from jenny.config.tool_schemas import TtsConfig

_ANY_ANDROID: Any = object()


def _ctx(
    *, android: Any = _ANY_ANDROID, enable: bool = True, with_config: bool = True
) -> Any:
    config = (
        SimpleNamespace(tts=SimpleNamespace(enable=enable))
        if with_config
        else SimpleNamespace()
    )
    return SimpleNamespace(android_context=android, config=config)


# ── Registrazione ──────────────────────────────────────────────────────────


def test_the_module_declares_exactly_the_two_voice_tools() -> None:
    assert {cls.name for cls in TOOLS} == {"speak", "stop_speaking"}


def test_the_tools_share_the_tts_config_key() -> None:
    for cls in TOOLS:
        assert cls.config_key == "tts"
        assert cls.config_cls() is TtsConfig


def test_both_tools_are_available_to_subagents() -> None:
    for cls in TOOLS:
        assert "subagent" in cls._scopes


def test_parameters_serialise_as_json() -> None:
    # Un parametro dichiarato male (p. es. ``description`` al posto di una
    # proprietà) sopravvive fino alla chiamata al provider e la fa fallire con
    # «Object of type StringSchema is not JSON serializable».
    for cls in TOOLS:
        assert isinstance(json.loads(json.dumps(cls.parameters.fget(cls))), dict)


# ── Attivazione ────────────────────────────────────────────────────────────


def test_enabled_requires_an_android_context() -> None:
    assert SpeakTool.enabled(_ctx(android=None)) is False
    assert SpeakTool.enabled(_ctx()) is True


def test_enabled_follows_the_user_toggle() -> None:
    assert SpeakTool.enabled(_ctx(enable=False)) is False
    assert SpeakTool.enabled(_ctx(enable=True)) is True


def test_enabled_is_false_when_the_config_section_is_missing() -> None:
    assert SpeakTool.enabled(_ctx(with_config=False)) is False


def test_every_tool_in_the_module_shares_the_same_gate() -> None:
    for cls in TOOLS:
        assert cls.enabled(_ctx()) is True
        assert cls.enabled(_ctx(android=None)) is False
        assert cls.enabled(_ctx(enable=False)) is False


# ── execute: solo inoltro al runtime ───────────────────────────────────────


@pytest.mark.asyncio
async def test_speak_returns_the_runtime_json(monkeypatch: Any) -> None:
    async def _fake_speak(text: str, language: str = "", rate: float = 1.0) -> dict[str, Any]:
        return {"ok": True, "spoke": text, "language": language, "rate": rate}

    monkeypatch.setattr(runtime_tts, "speak", _fake_speak)

    tool = SpeakTool(config=SimpleNamespace())
    payload = json.loads(await tool.execute(text="ciao", language="uk-UA", rate=1.2))

    assert payload == {"ok": True, "spoke": "ciao", "language": "uk-UA", "rate": 1.2}


@pytest.mark.asyncio
async def test_speak_maps_a_none_runtime_result_to_tts_unavailable(monkeypatch: Any) -> None:
    async def _fake_speak(text: str, language: str = "", rate: float = 1.0) -> None:
        return None

    monkeypatch.setattr(runtime_tts, "speak", _fake_speak)

    tool = SpeakTool(config=SimpleNamespace())

    assert json.loads(await tool.execute(text="ciao")) == {
        "ok": False,
        "error": "tts_unavailable",
    }


@pytest.mark.asyncio
async def test_stop_speaking_returns_the_runtime_json(monkeypatch: Any) -> None:
    async def _fake_stop() -> dict[str, Any]:
        return {"ok": True}

    monkeypatch.setattr(runtime_tts, "stop", _fake_stop)

    tool = StopSpeakingTool(config=SimpleNamespace())

    assert json.loads(await tool.execute()) == {"ok": True}


@pytest.mark.asyncio
async def test_a_raising_runtime_becomes_a_json_error(monkeypatch: Any) -> None:
    async def _boom(text: str, language: str = "", rate: float = 1.0) -> None:
        raise RuntimeError("bridge ko")

    monkeypatch.setattr(runtime_tts, "speak", _boom)

    tool = SpeakTool(config=SimpleNamespace())
    payload = json.loads(await tool.execute(text="ciao"))

    assert payload["ok"] is False
    assert payload["error"].startswith("speak:")
