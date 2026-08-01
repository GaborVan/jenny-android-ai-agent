"""Tests for the gateway entry point."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jenny.android_entry import run_gateway
from jenny.config.bootstrap import ensure_minimal_config
from jenny.runtime.context import get_runtime_context


def test_run_gateway_prepares_workspace_and_passes_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """run_gateway should create workspace, sync templates, ensure config,
    and forward host/port/ws_port=port to _run_gateway."""
    mock_run = AsyncMock()

    # Il workspace vive nel RuntimeContext; monkeypatch ripristina la sessione.
    monkeypatch.setattr(get_runtime_context(), "workspace_dir", None)

    with patch("jenny.gateway_runtime._run_gateway", new=mock_run):
        run_gateway(
            str(tmp_path),
            host="127.0.0.1",
            port=18000,
        )

    workspace = tmp_path / "workspace"
    assert workspace.exists()
    assert (workspace / "config.json").exists()
    assert (workspace / "SOUL.md").exists()

    mock_run.assert_awaited_once_with(
        config=None,
        host="127.0.0.1",
        port=18000,
        ws_port=18000,
    )


def test_ensure_minimal_config_writes_minimal_json(tmp_path: Path):
    """ensure_minimal_config should write a minimal config when missing."""
    ensure_minimal_config(tmp_path)

    path = tmp_path / "config.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gateway"]["host"] == "127.0.0.1"
    ws = data["websocket"]
    assert ws["enabled"] is True
    assert "channels" not in data


def test_ensure_minimal_config_uses_existing_workspace(tmp_path: Path):
    """The config should land inside the provided workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_minimal_config(workspace)
    path = workspace / "config.json"
    assert path.exists()


def test_ensure_minimal_config_is_idempotent(tmp_path: Path):
    """ensure_minimal_config should not overwrite an existing config."""
    ensure_minimal_config(tmp_path)
    path = tmp_path / "config.json"
    original = path.read_text(encoding="utf-8")

    ensure_minimal_config(tmp_path)
    assert path.read_text(encoding="utf-8") == original


