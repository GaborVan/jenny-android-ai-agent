"""Test per ``jenny/runtime/stt.py`` (riconoscimento vocale di sistema).

Fuori da Android tutto degrada a ``None`` senza sollevare; con un context finto
e il bridge finto montato sul seam ``_resolve_bridge_class``, i risultati JSON
del bridge arrivano parsati al chiamante. La normalizzazione della lingua e il
controllo che il file esista vivono in Python, e vengono provati qui: è lì che i
parametri liberi vengono filtrati prima di raggiungere il codice Kotlin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import jenny.runtime.stt as stt


@pytest.fixture(autouse=True)
def _fresh_bridge() -> Any:
    """Ogni test parte da una cache del bridge vuota (v. ``test_tts``)."""
    stt.reset_stt_state()
    yield
    stt.reset_stt_state()


def _mount_fake_bridge(
    monkeypatch: Any, reply: str = '{"ok":true,"text":"ciao"}'
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    class _FakeBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def transcribeFile(self, path: str, language: str, offline: bool) -> str:  # noqa: N802
            calls.append((path, language, offline))
            return reply

    monkeypatch.setattr(stt, "get_android_context", lambda: object())
    monkeypatch.setattr(stt, "_resolve_bridge_class", lambda: _FakeBridge)
    return calls


# ── Fuori da Android ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_is_none_without_android_context(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(stt, "get_android_context", lambda: None)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    assert await stt.transcribe_file(str(audio)) is None
    assert await stt.transcribe_file(str(tmp_path / "missing.ogg")) is None


# ── Normalizzazione della lingua ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("it", "it-IT"),
        ("en", "en-US"),
        ("uk", "uk-UA"),
        ("uk-UA", "uk-UA"),
        ("uk_UA", "uk-UA"),
        ("zh-Hans-CN", "zh-Hans-CN"),
        ("presto", ""),
        ("ukr!", ""),
        ("1", ""),
    ],
)
def test_language_tag_normalisation(value: str, expected: str) -> None:
    assert stt.language_tag_for(value) == expected


# ── Percorso felice ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transcribe_forwards_path_language_and_flag(monkeypatch: Any, tmp_path: Path) -> None:
    calls = _mount_fake_bridge(monkeypatch)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    result = await stt.transcribe_file(str(audio), language="uk", prefer_offline=True)

    assert result == {"ok": True, "text": "ciao"}
    assert calls == [(str(audio), "uk-UA", True)]


@pytest.mark.asyncio
async def test_prefer_offline_defaults_to_false(monkeypatch: Any, tmp_path: Path) -> None:
    calls = _mount_fake_bridge(monkeypatch)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    await stt.transcribe_file(str(audio), language="it")

    assert calls == [(str(audio), "it-IT", False)]


# ── Rifiuti in Python, prima del bridge ────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_file_is_refused_before_the_bridge(monkeypatch: Any, tmp_path: Path) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await stt.transcribe_file(str(tmp_path / "nope.ogg")) == {
        "ok": False,
        "error": "file_not_found",
    }
    assert await stt.transcribe_file("") == {"ok": False, "error": "file_not_found"}
    assert calls == []


@pytest.mark.asyncio
async def test_a_directory_is_not_a_file(monkeypatch: Any, tmp_path: Path) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await stt.transcribe_file(str(tmp_path)) == {
        "ok": False,
        "error": "file_not_found",
    }
    assert calls == []


# ── Il bridge che fallisce o risponde male ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_raising_bridge_becomes_bridge_unavailable(monkeypatch: Any, tmp_path: Path) -> None:
    class _BrokenBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def transcribeFile(self, path: str, language: str, offline: bool) -> str:  # noqa: N802
            raise RuntimeError("boom")

    monkeypatch.setattr(stt, "get_android_context", lambda: object())
    monkeypatch.setattr(stt, "_resolve_bridge_class", lambda: _BrokenBridge)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    assert await stt.transcribe_file(str(audio)) == {
        "ok": False,
        "error": "bridge_unavailable",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "not json", '"text"', "[]"])
async def test_a_non_json_reply_becomes_bridge_unavailable(
    monkeypatch: Any, tmp_path: Path, reply: str
) -> None:
    _mount_fake_bridge(monkeypatch, reply)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    assert await stt.transcribe_file(str(audio)) == {
        "ok": False,
        "error": "bridge_unavailable",
    }


@pytest.mark.asyncio
async def test_the_engine_refusing_is_passed_through(monkeypatch: Any, tmp_path: Path) -> None:
    _mount_fake_bridge(
        monkeypatch,
        '{"ok":false,"error":"stt_file_source_unsupported","hint":"needs Android 13"}',
    )
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"x")

    result = await stt.transcribe_file(str(audio))

    assert result == {
        "ok": False,
        "error": "stt_file_source_unsupported",
        "hint": "needs Android 13",
    }
