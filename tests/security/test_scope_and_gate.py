"""Fase 2.1/2.3 — context manager di scope + delega del gate WebUI."""

from __future__ import annotations

import pytest

from jenny.security.workspace_access import (
    current_workspace_scope,
    enter_workspace_scope,
)
from jenny.webui.workspace_files import validate_path


class _Scope:
    """Oggetto scope minimale: enter_workspace_scope tratta lo scope come opaco."""


def test_enter_workspace_scope_binds_then_resets() -> None:
    assert current_workspace_scope() is None
    scope = _Scope()
    with enter_workspace_scope(scope):
        assert current_workspace_scope() is scope
    assert current_workspace_scope() is None


def test_enter_workspace_scope_resets_on_exception() -> None:
    scope = _Scope()
    with pytest.raises(RuntimeError):
        with enter_workspace_scope(scope):
            assert current_workspace_scope() is scope
            raise RuntimeError("boom")
    assert current_workspace_scope() is None


def test_enter_workspace_scope_none_is_noop() -> None:
    assert current_workspace_scope() is None
    with enter_workspace_scope(None):
        assert current_workspace_scope() is None
    assert current_workspace_scope() is None


def test_webui_validate_path_delegates_and_blocks_traversal(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "ok.txt").write_text("x", encoding="utf-8")

    # Path interno: ok.
    assert validate_path(root, "ok.txt") == (root / "ok.txt").resolve()
    # Traversal: contratto storico ValueError (ora via il gate unico del core).
    with pytest.raises(ValueError):
        validate_path(root, "../escape.txt")
