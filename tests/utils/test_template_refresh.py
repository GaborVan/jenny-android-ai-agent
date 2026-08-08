"""I prompt di sistema si aggiornano, i file dell'utente no.

Il difetto osservato in produzione: `sync_workspace_templates` estraeva tutto
`jenny/templates/` con `skip_existing=True`, per non calpestare `SOUL.md` e
`USER.md`. Effetto collaterale, invisibile perché sembra funzionare: anche i
prompt di sistema erano congelati. Un telefono aggiornato per mesi girava con
`identity.md` della versione in cui era stato installato — un file *nuovo*
arrivava, un file *corretto* no.

Verificato sul dispositivo il 2026-08-06: dopo un aggiornamento con tre prompt
modificati e uno aggiunto, il log diceva `Extracted 1 files`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.utils.android_assets import (
    _SYSTEM_PROMPT_TEMPLATES,
    _TEMPLATES_MANIFEST,
    _USER_OWNED_TEMPLATES,
    extract_package_dir,
)
from jenny.utils.helpers import sync_workspace_templates

MARKER = "MODIFICATO A MANO — non deve sopravvivere a un aggiornamento\n"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(root, silent=True)
    return root


# -- le due politiche -------------------------------------------------------


def test_a_system_prompt_is_restored_on_the_next_sync(workspace: Path) -> None:
    """Il caso che il difetto rendeva impossibile: una correzione che arriva."""
    target = workspace / "agent" / "identity.md"
    original = target.read_text(encoding="utf-8")
    target.write_text(MARKER, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("name", _USER_OWNED_TEMPLATES)
def test_a_user_file_is_never_overwritten(workspace: Path, name: str) -> None:
    """L'altra metà, e il motivo per cui il difetto esisteva.

    ``SOUL.md`` e ``USER.md`` li riscrive Dream, ``AGENTS.md`` e
    ``HEARTBEAT.md`` l'utente: la copia del pacchetto è un punto di partenza,
    non la verità. Riscriverli cancellerebbe la personalità del bot.
    """
    target = workspace / name
    target.write_text(MARKER, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == MARKER


def test_every_prompt_under_agent_is_treated_as_a_system_prompt(workspace: Path) -> None:
    """Chi aggiunge un prompt non deve doversi ricordare di questa distinzione.

    La regola è posizionale: tutto ciò che sta sotto ``agent/`` è codice.
    Se una voce nuova finisce nella lista sbagliata, l'aggiornamento smette di
    arrivare per quel file soltanto — il tipo di guasto che nessuno nota.
    """
    for name in _SYSTEM_PROMPT_TEMPLATES:
        assert name.startswith("agent/"), f"{name} non è un prompt di sistema"
    for name in _USER_OWNED_TEMPLATES:
        assert not name.startswith("agent/"), f"{name} non è un file dell'utente"


def test_the_two_lists_cover_the_manifest_exactly(workspace: Path) -> None:
    """Nessun file può cadere fra le due politiche, né essere in entrambe."""
    assert set(_USER_OWNED_TEMPLATES).isdisjoint(_SYSTEM_PROMPT_TEMPLATES)
    assert set(_USER_OWNED_TEMPLATES) | set(_SYSTEM_PROMPT_TEMPLATES) == set(
        _TEMPLATES_MANIFEST
    )


# -- il sottoinsieme dichiarato ---------------------------------------------


def test_extracting_a_file_outside_the_manifest_is_an_error(tmp_path: Path) -> None:
    """``only`` resta un sottoinsieme dichiarato, non una scorciatoia.

    Il manifest esiste perché sul dispositivo nessun file arrivi o manchi senza
    traccia; un ``only`` capace di aggirarlo riaprirebbe quella porta.
    """
    with pytest.raises(ValueError, match="not in the manifest"):
        extract_package_dir(
            "jenny.templates", tmp_path, only=["agent/does_not_exist.md"],
        )
