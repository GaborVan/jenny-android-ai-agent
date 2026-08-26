"""Test del ``RuntimeContext`` (unica fonte di verità dello stato di runtime).

Verifica che gli accessor storici (``jenny.config.paths``, ``android_entry``)
deleghino davvero all'holder unico e che i campi si comportino da contratto.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.android import force_android_context, force_no_android_context

from jenny.runtime.context import get_android_context, get_runtime_context


def test_runtime_context_is_a_singleton() -> None:
    assert get_runtime_context() is get_runtime_context()


def test_android_context_accessor_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = force_android_context(monkeypatch)
    assert get_android_context() is sentinel
    force_no_android_context(monkeypatch)
    assert get_android_context() is None


def test_android_entry_set_android_context_writes_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jenny import android_entry

    force_no_android_context(monkeypatch)
    sentinel = object()
    android_entry.set_android_context(sentinel)
    try:
        assert get_runtime_context().android_context is sentinel
        # L'accessor re-esportato da android_entry legge lo stesso holder.
        assert android_entry.get_android_context() is sentinel
    finally:
        get_runtime_context().android_context = None


def test_workspace_accessors_delegate_to_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legge l'holder, e **non crea niente**.

    L'asserzione era rovesciata («get_workspace_path crea la dir») e fissava un
    difetto: un accessor che chiama ``ensure_dir`` fa un ``mkdir`` sulla radice
    del workspace a ogni chiamata, e dentro ``python_exec`` quella è una
    scrittura fuori dalla cartella del progetto — rifiutata dalla guardia, e per
    questo il 26/08 ``wiki_lint`` non era eseguibile in nessun turno di progetto.
    Invertita, non cancellata: chi rimettesse l'``ensure_dir`` cade qui.
    """
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", tmp_path / "ws")
    assert paths_mod.get_workspace_path() == tmp_path / "ws"
    assert not (tmp_path / "ws").exists()


def test_workspace_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    with pytest.raises(RuntimeError, match="set_workspace_dir"):
        paths_mod.get_workspace_path()


def test_set_workspace_dir_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from jenny.config import paths as paths_mod

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)
    paths_mod.set_workspace_dir(str(tmp_path / "nuovo"))
    assert get_runtime_context().workspace_dir == tmp_path / "nuovo"
    # Stringa vuota = nessun workspace (usato dal teardown dei test di sessione).
    paths_mod.set_workspace_dir("")
    assert get_runtime_context().workspace_dir is None


def test_device_timezone_default_is_none() -> None:
    from dataclasses import fields

    from jenny.runtime.context import RuntimeContext

    defaults = {f.name: f.default for f in fields(RuntimeContext)}
    assert defaults["device_timezone"] is None
    assert defaults["android_context"] is None
    assert defaults["workspace_dir"] is None
