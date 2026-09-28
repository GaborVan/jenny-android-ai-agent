"""Test per ``jenny/runtime/tts.py`` (sintesi vocale di sistema).

Fuori da Android tutto degrada a no-op senza sollevare (``None``); con un
context finto e il bridge finto montato sul seam ``_resolve_bridge_class``, i
risultati JSON del bridge arrivano parsati al chiamante.

La seconda meta' di questo file copre la validazione che vive **in Python**
(testo vuoto, lunghezza, tag di lingua, range del rate): e' li' che i parametri
liberi del modello vengono filtrati prima di raggiungere il codice Kotlin, e
per questo i test verificano anche che il bridge, nei casi rifiutati, non venga
chiamato affatto.
"""

from __future__ import annotations

from typing import Any

import pytest

import jenny.runtime.tts as tts


@pytest.fixture(autouse=True)
def _fresh_bridge() -> Any:
    """Ogni test parte da una cache del bridge vuota.

    ``BridgeCache`` tiene l'istanza a livello di modulo: senza il reset il
    secondo test riuserebbe il finto bridge del primo — e il suo registratore di
    chiamate con lui.
    """
    tts.reset_tts_state()
    yield
    tts.reset_tts_state()


def _mount_fake_bridge(monkeypatch: Any) -> list[tuple[Any, ...]]:
    """Monta un finto ``TtsBridge`` e torna la lista delle chiamate ricevute.

    Il finto costruttore accetta il context (``BridgeCache`` costruisce la classe
    con l'Android Context: senza quel parametro solleva ``TypeError``).
    """
    calls: list[tuple[Any, ...]] = []

    class _FakeBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def speak(self, text: str, language: str, rate: float) -> str:
            calls.append(("speak", text, language, rate))
            return '{"ok":true}'

        def stop(self) -> str:
            calls.append(("stop",))
            return '{"ok":true}'

    monkeypatch.setattr(tts, "get_android_context", lambda: object())
    monkeypatch.setattr(tts, "_resolve_bridge_class", lambda: _FakeBridge)
    return calls


# ── Fuori da Android: tutto None ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_speak_and_stop_return_none_without_android_context(monkeypatch: Any) -> None:
    monkeypatch.setattr(tts, "get_android_context", lambda: None)
    assert await tts.speak("ciao") is None
    assert await tts.stop() is None


# ── Percorso felice ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_speak_forwards_text_language_and_rate(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    result = await tts.speak("Ciao, come stai?", "uk-UA", 1.25)

    assert result == {"ok": True}
    assert calls == [("speak", "Ciao, come stai?", "uk-UA", 1.25)]


@pytest.mark.asyncio
async def test_empty_language_is_the_engine_default(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await tts.speak("ciao") == {"ok": True}
    assert calls == [("speak", "ciao", "", 1.0)]


@pytest.mark.asyncio
async def test_stop_forwards_to_the_bridge(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await tts.stop() == {"ok": True}
    assert calls == [("stop",)]


# ── Validazione in Python: il bridge non deve nemmeno vedere questi input ──


@pytest.mark.asyncio
async def test_empty_text_is_refused_before_the_bridge(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await tts.speak("   ") == {"ok": False, "error": "empty_text"}
    assert calls == []


@pytest.mark.asyncio
async def test_text_over_the_cap_is_refused_before_the_bridge(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    result = await tts.speak("a" * (tts.MAX_SPEAK_CHARS + 1))

    assert result is not None
    assert result["error"] == "text_too_long"
    assert "hint" in result
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["ukr!", "uk UA", "<script>", "en--US", "uk_UA"])
async def test_invalid_language_is_refused_before_the_bridge(
    monkeypatch: Any, bad: str
) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await tts.speak("ciao", bad) == {"ok": False, "error": "invalid_language"}
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("good", ["it", "uk-UA", "en-US", "zh-Hans-CN"])
async def test_well_formed_language_tags_pass(monkeypatch: Any, good: str) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    assert await tts.speak("ciao", good) == {"ok": True}
    assert calls == [("speak", "ciao", good, 1.0)]


# ── Rate: clamp e valori non numerici ──────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_is_clamped_to_the_supported_range(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    await tts.speak("ciao", "", 0.1)
    await tts.speak("ciao", "", 9.0)

    assert [call[3] for call in calls] == [tts.MIN_RATE, tts.MAX_RATE]


@pytest.mark.asyncio
async def test_a_numeric_string_rate_is_coerced(monkeypatch: Any) -> None:
    # Gli argomenti dei tool arrivano da JSON: un numero puo' presentarsi come
    # stringa, e ``float()`` lo accetta.
    calls = _mount_fake_bridge(monkeypatch)

    await tts.speak("ciao", "", "1.5")  # type: ignore[arg-type]

    assert calls == [("speak", "ciao", "", 1.5)]


@pytest.mark.asyncio
async def test_a_non_numeric_rate_falls_back_to_the_default(monkeypatch: Any) -> None:
    calls = _mount_fake_bridge(monkeypatch)

    await tts.speak("ciao", "", "presto")  # type: ignore[arg-type]

    assert calls == [("speak", "ciao", "", 1.0)]


# ── Il bridge che fallisce o risponde male ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_raising_bridge_becomes_bridge_unavailable(monkeypatch: Any) -> None:
    class _BrokenBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def speak(self, text: str, language: str, rate: float) -> str:
            raise RuntimeError("boom")

        def stop(self) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(tts, "get_android_context", lambda: object())
    monkeypatch.setattr(tts, "_resolve_bridge_class", lambda: _BrokenBridge)

    assert await tts.speak("ciao") == {"ok": False, "error": "bridge_unavailable"}
    assert await tts.stop() == {"ok": False, "error": "bridge_unavailable"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["", "not json", '"a string"', "[]"])
async def test_a_non_json_reply_becomes_bridge_unavailable(
    monkeypatch: Any, reply: str
) -> None:
    class _GarbageBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def speak(self, text: str, language: str, rate: float) -> str:
            return reply

        def stop(self) -> str:
            return reply

    monkeypatch.setattr(tts, "get_android_context", lambda: object())
    monkeypatch.setattr(tts, "_resolve_bridge_class", lambda: _GarbageBridge)

    assert await tts.speak("ciao") == {"ok": False, "error": "bridge_unavailable"}
    assert await tts.stop() == {"ok": False, "error": "bridge_unavailable"}


@pytest.mark.asyncio
async def test_the_engine_being_unavailable_is_passed_through(monkeypatch: Any) -> None:
    payload = '{"ok":false,"error":"tts_unavailable","hint":"install a TTS engine"}'

    class _NoEngineBridge:
        def __init__(self, context: object | None = None) -> None:
            self._context = context

        def speak(self, text: str, language: str, rate: float) -> str:
            return payload

    monkeypatch.setattr(tts, "get_android_context", lambda: object())
    monkeypatch.setattr(tts, "_resolve_bridge_class", lambda: _NoEngineBridge)

    result = await tts.speak("ciao")

    assert result == {
        "ok": False,
        "error": "tts_unavailable",
        "hint": "install a TTS engine",
    }
