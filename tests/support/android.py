"""Fake del boundary Android: RuntimeContext e modulo ``java`` di Chaquopy.

Consolida i pattern usati sparsi nella suite (monkeypatch di
``get_android_context``, finto modulo ``java`` in ``sys.modules``) in helper
riusabili. Il ``RuntimeContext`` è l'unica fonte di verità dello stato di
runtime, quindi patchare lì copre tutti gli accessor delegati.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest

from jenny.runtime.context import get_runtime_context


def force_android_context(monkeypatch: pytest.MonkeyPatch, context: Any | None = None) -> Any:
    """Simula il runtime Android: ``get_android_context()`` ritorna un oggetto.

    Ritorna il contesto installato (un ``object()`` opaco se non fornito).
    """
    ctx = context if context is not None else object()
    monkeypatch.setattr(get_runtime_context(), "android_context", ctx)
    return ctx


def force_no_android_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula il runtime host: ``get_android_context()`` ritorna None."""
    monkeypatch.setattr(get_runtime_context(), "android_context", None)


def fake_java_module(
    monkeypatch: pytest.MonkeyPatch, classes: dict[str, Any]
) -> ModuleType:
    """Monta un finto modulo ``java`` con ``jclass`` che risolve da ``classes``.

    Generalizza il pattern di ``tests/utils/test_device_timezone.py``: il
    codice sotto test può fare ``from java import jclass`` come sotto
    Chaquopy. Le chiavi di ``classes`` sono i nomi Java completi
    (es. ``"javax.crypto.Cipher"``).
    """
    module = ModuleType("java")

    def jclass(name: str) -> Any:
        try:
            return classes[name]
        except KeyError:
            raise ValueError(f"fake jclass: classe non registrata {name!r}") from None

    module.jclass = jclass  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "java", module)
    return module
