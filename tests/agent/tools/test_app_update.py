"""Test dei tool di aggiornamento (``jenny/agent/tools/app_update.py``).

L'affaccio LLM non contiene logica: quello che si verifica è che i due tool
esistano solo dentro l'app Android, che la lettura resti davvero una lettura, e
che l'installazione sia difficile da innescare per sbaglio — perché l'errore che
si vuole prevenire (un riavvio a metà conversazione) non è recuperabile.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from jenny.agent.tools.app_update import InstallUpdateTool, UpdateStatusTool
from jenny.agent.tools.loader import ToolLoader
from jenny.runtime import update_check, update_install
from jenny.runtime.update_check import UpdateInfo
from jenny.runtime.update_install import InstallResult

_INFO = UpdateInfo(
    version_code=9,
    version_name="0.7.0",
    apk_url="https://example.invalid/jenny-0.7.0.apk",
    sha256="b" * 64,
    size=48210944,
    notes_url="https://example.invalid/notes",
    summary="Aggiornamenti in-app.",
    critical=True,
)


def _ctx(*, android: Any = object()) -> Any:
    return SimpleNamespace(android_context=android, config=SimpleNamespace())


@pytest.fixture(autouse=True)
def clean_install_state() -> Any:
    update_install.reset_install_state()
    yield
    update_install.reset_install_state()


# --------------------------------------------------------------------------
# Registrazione
# --------------------------------------------------------------------------


def test_module_is_registered_in_the_loader() -> None:
    """Un modulo tool non nella lista del loader semplicemente non esiste."""
    discovered = {cls.__name__ for cls in ToolLoader().discover()}
    assert {"UpdateStatusTool", "InstallUpdateTool"} <= discovered


def test_tools_are_android_only() -> None:
    assert UpdateStatusTool.enabled(_ctx()) is True
    assert InstallUpdateTool.enabled(_ctx()) is True
    assert UpdateStatusTool.enabled(_ctx(android=None)) is False
    assert InstallUpdateTool.enabled(_ctx(android=None)) is False
    assert "Android" in (InstallUpdateTool.disabled_reason(_ctx(android=None)) or "")


def test_tools_are_never_handed_to_subagents() -> None:
    assert "subagent" not in UpdateStatusTool._scopes
    assert "subagent" not in InstallUpdateTool._scopes


def test_install_description_warns_about_the_restart() -> None:
    """La policy vive nella descrizione: è l'unica cosa che il modello legge."""
    description = InstallUpdateTool.description.lower()
    assert "restarts" in description
    assert "explicitly asked" in description
    assert "never on your own initiative" in description


def test_only_the_read_tool_is_read_only() -> None:
    assert UpdateStatusTool().read_only is True
    assert InstallUpdateTool().read_only is False
    assert InstallUpdateTool().exclusive is True


# --------------------------------------------------------------------------
# update_status
# --------------------------------------------------------------------------


async def test_status_reports_the_cached_update(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "cached_update", lambda language=None: _INFO)
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    payload = json.loads(await UpdateStatusTool().execute())

    assert payload["updateAvailable"] is True
    assert payload["installedVersionCode"] == 8
    assert payload["update"]["versionName"] == "0.7.0"
    assert payload["update"]["versionCode"] == 9
    assert payload["update"]["critical"] is True
    assert payload["install"]["phase"] == "idle"


async def test_status_says_so_when_there_is_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_check, "cached_update", lambda language=None: None)
    monkeypatch.setattr(update_check, "installed_version_code", lambda: 8)

    payload = json.loads(await UpdateStatusTool().execute())

    assert payload["updateAvailable"] is False
    assert payload["update"] is None


# --------------------------------------------------------------------------
# install_update
# --------------------------------------------------------------------------


async def test_install_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def _never(info: Any = None) -> InstallResult:
        nonlocal called
        called = True
        return InstallResult(True, "silent", "")

    monkeypatch.setattr(update_install, "start_install", _never)

    payload = json.loads(await InstallUpdateTool().execute(confirm=False))

    assert payload["ok"] is False
    assert payload["state"] == "error"
    assert called is False


async def test_install_forwards_the_runtime_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _prompt(info: Any = None) -> InstallResult:
        return InstallResult(True, "prompt", "the system installer is on screen")

    monkeypatch.setattr(update_install, "start_install", _prompt)

    payload = json.loads(await InstallUpdateTool().execute(confirm=True))

    assert payload == {
        "ok": True,
        "state": "prompt",
        "detail": "the system installer is on screen",
    }


async def test_install_surfaces_a_refusal_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Niente in cache: l'agente deve poterlo raccontare, non morire sul tool."""
    monkeypatch.setattr(update_check, "cached_update", lambda language=None: None)

    payload = json.loads(await InstallUpdateTool().execute(confirm=True))

    assert payload["ok"] is False
    assert payload["state"] == "error"
    assert "No update is available" in payload["detail"]
