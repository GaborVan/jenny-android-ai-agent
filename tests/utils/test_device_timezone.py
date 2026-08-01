"""Test per detect_device_timezone (bridge Java simulato via sys.modules)."""

import sys
import types

import pytest

from jenny.utils.device_timezone import detect_device_timezone


def _fake_java(get_id) -> types.SimpleNamespace:
    """Costruisce un finto modulo ``java`` con jclass('java.util.TimeZone')."""

    class _FakeTimeZone:
        @staticmethod
        def getDefault():  # noqa: N802 — replica l'API Java
            return types.SimpleNamespace(getID=get_id)

    def jclass(name: str):
        assert name == "java.util.TimeZone"
        return _FakeTimeZone

    return types.SimpleNamespace(jclass=jclass)


def test_returns_none_on_host() -> None:
    # Su host il modulo ``java`` (Chaquopy) non esiste.
    assert "java" not in sys.modules
    assert detect_device_timezone() is None


def test_returns_device_id_with_fake_java(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "java", _fake_java(lambda: "Europe/Rome"))
    assert detect_device_timezone() == "Europe/Rome"


def test_returns_none_when_bridge_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("JNI error")

    monkeypatch.setitem(sys.modules, "java", _fake_java(_boom))
    assert detect_device_timezone() is None


def test_returns_none_for_empty_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "java", _fake_java(lambda: "  "))
    assert detect_device_timezone() is None
