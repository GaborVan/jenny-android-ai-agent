"""I prompt di sistema si aggiornano, i file dell'utente no.

Il difetto osservato in produzione: `sync_workspace_templates` estraeva tutto
`jenny/templates/` con `skip_existing=True`, per non calpestare `SOUL.md` e
`USER.md`. Effetto collaterale, invisibile perché sembra funzionare: anche i
prompt di sistema erano congelati. Un telefono aggiornato per mesi girava con
`identity.md` della versione in cui era stato installato — un file *nuovo*
arrivava, un file *corretto* no.

Verificato sul dispositivo il 2026-08-06: dopo un aggiornamento con tre prompt
modificati e uno aggiunto, il log diceva `Extracted 1 files`.

Le politiche sono tre e questo file le documenta tutte: il file dell'utente che
si crea una volta sola, il prompt di sistema che si riscrive a ogni avvio, e in
mezzo il ritiro sul posto — l'unica scrittura dentro un file dell'utente, e solo
quando quel file è ancora, byte per byte, una versione nostra ritirata.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from jenny.utils.android_assets import (
    _RETIRED_TEMPLATE_DIGESTS,
    _SYSTEM_PROMPT_TEMPLATES,
    _TEMPLATES_MANIFEST,
    _USER_OWNED_TEMPLATES,
    extract_package_dir,
    retire_withdrawn_templates,
)
from jenny.utils.helpers import load_bundled_template, sync_workspace_templates

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


# -- la terza politica: ritiro sul posto ------------------------------------
#
# Riconoscere una versione ritirata la tiene fuori dal prompt ma la lascia sul
# disco, e lì resta a un tasto dall'essere peggio di prima: la prima riga che
# l'utente ci aggiunge promuove tutto il manuale ritirato a "scritto
# dall'utente", per sempre e senza etichetta. Qui quel testo se ne va davvero —
# ma solo quando è, byte per byte, ancora nostro.


def _retired_fixture(name: str) -> str:
    """Un template ritirato, letto da ``tests/agent/fixtures/``.

    Stesso motivo per cui li legge di lì ``test_context_builder``: alcune di
    quelle righe finiscono con uno spazio, e trascritte in un sorgente Python
    ``ruff`` (W291) le pulirebbe. Il digest non combacerebbe più con quello che
    c'è sui telefoni e il test proverebbe qualcos'altro.
    """
    return (Path(__file__).resolve().parents[1] / "agent" / "fixtures" / name).read_text(
        encoding="utf-8"
    )


# Ogni versione ritirata elencata nel registro, con il file che la contiene.
# Sono tutte candidate vive: un telefono porta per sempre quella che era bundled
# al *suo* primo avvio, indipendentemente da quanti aggiornamenti ha preso dopo.
_RETIRED_FIXTURES = [
    ("AGENTS.md", "agents_md_retired_v0.3.0.md"),
    ("AGENTS.md", "agents_md_retired_6c5dba8_unreleased.md"),
    ("AGENTS.md", "agents_md_retired_v0.6.6.md"),
    ("USER.md", "user_md_retired_0.3.0.md"),
]


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_a_withdrawn_version_of_ours_is_rewritten(
    workspace: Path, name: str, fixture: str
) -> None:
    """Il caso del Titan 2: un file dell'utente che è ancora roba nostra, ritirata."""
    target = workspace / name
    target.write_text(_retired_fixture(fixture), encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == load_bundled_template(name)


@pytest.mark.parametrize(("name", "fixture"), _RETIRED_FIXTURES)
def test_one_line_of_the_users_own_is_enough_to_stop_it(
    workspace: Path, name: str, fixture: str
) -> None:
    """Questo è il test che protegge l'utente, ed è il motivo per cui il ritiro
    non chiede né uno snapshot né un consenso.

    La condizione è un digest esatto: una riga aggiunta in fondo e il file non è
    più nostro, quindi non si tocca. Non è prudenza applicata bene, è
    impossibilità per costruzione — chi allentasse il confronto (match
    approssimato, "quasi uguale", prefisso) cancellerebbe testo che l'utente ha
    scritto e non ha altrove.
    """
    target = workspace / name
    edited = _retired_fixture(fixture) + "\n- Deploy con `./gradlew`.\n"
    target.write_text(edited, encoding="utf-8")

    sync_workspace_templates(workspace, silent=True)

    assert target.read_text(encoding="utf-8") == edited


@pytest.mark.parametrize("name", sorted(_RETIRED_TEMPLATE_DIGESTS))
def test_a_file_already_current_is_not_written_at_all(workspace: Path, name: str) -> None:
    """Non "riscritto uguale": proprio non toccato.

    L'asserzione è sulla mtime e non sul contenuto perché una riscrittura con
    byte identici passerebbe un controllo sul contenuto ed è comunque un difetto:
    fa I/O inutile su ogni boot e stampa una riga di log che dichiara una
    migrazione mai avvenuta.
    """
    target = workspace / name
    os.utime(target, (1_000_000, 1_000_000))
    before = target.stat().st_mtime_ns

    sync_workspace_templates(workspace, silent=True)

    assert target.stat().st_mtime_ns == before


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Un workspace vuoto non ha niente da ritirare: il seeding lo crea dopo."""
    assert retire_withdrawn_templates(tmp_path) == []
    assert not (tmp_path / "AGENTS.md").exists()


def test_an_unreadable_bundle_leaves_the_file_alone(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prima si leggono i byte nuovi, poi si scrive.

    L'ordine è ciò che garantisce che non esista un istante in cui il file
    dell'utente non c'è: senza il contenuto da mettere, non si cancella niente.
    """
    retired = _retired_fixture("agents_md_retired_v0.3.0.md")
    (workspace / "AGENTS.md").write_text(retired, encoding="utf-8")
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: None
    )

    assert retire_withdrawn_templates(workspace) == []

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == retired


def test_an_empty_bundle_leaves_the_file_alone(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vuoto è illeggibile, e sbagliarlo è irreversibile.

    ``read_asset`` ritorna ``b""`` per un asset presente ma troncato. Con una
    guardia sul solo ``None`` quei zero byte finivano nel file dell'utente, e da
    lì non tornava indietro: a zero byte non combacia con nessun digest ritirato
    (quindi nessun boot successivo lo riprova) e continua a esistere (quindi
    l'estrazione ``skip_existing`` gli passa accanto). Il file resta vuoto finché
    non se ne accorge una persona.
    """
    retired = _retired_fixture("agents_md_retired_v0.3.0.md")
    (workspace / "AGENTS.md").write_text(retired, encoding="utf-8")
    monkeypatch.setattr(
        "jenny.utils.android_assets.read_asset", lambda *args, **kwargs: b""
    )

    assert retire_withdrawn_templates(workspace) == []

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == retired


def test_the_retired_file_keeps_its_permissions(workspace: Path) -> None:
    """Il ritiro porta via del testo nostro, non i permessi dell'utente.

    ``atomic_write`` scrive un file nuovo e lo mette al posto del vecchio: nasce
    col umask del processo, quindi un file tenuto a 0600 si ritroverebbe a 0644.
    Allargare i permessi di un file dell'utente è un secondo effetto che nessuno
    ha chiesto — ``config/store.py`` rimette il chmod a mano per lo stesso
    motivo.
    """
    target = workspace / "AGENTS.md"
    target.write_text(_retired_fixture("agents_md_retired_v0.3.0.md"), encoding="utf-8")
    target.chmod(0o600)

    assert retire_withdrawn_templates(workspace) == ["AGENTS.md"]

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_a_symlinked_file_is_not_retired(workspace: Path, tmp_path: Path) -> None:
    """Un symlink è una decisione, e il ritiro non la ribalta.

    ``atomic_write`` fa ``os.replace`` sul path: al posto del link resterebbe un
    file regolare, l'utente perderebbe il collegamento che aveva scelto e le due
    copie divergerebbero senza dirlo a nessuno. Rinunciare è la mossa giusta —
    il ritiro è un'ottimizzazione, il link no.
    """
    real = tmp_path / "AGENTS-condiviso.md"
    retired = _retired_fixture("agents_md_retired_v0.3.0.md")
    real.write_text(retired, encoding="utf-8")
    target = workspace / "AGENTS.md"
    target.unlink()
    target.symlink_to(real)

    assert retire_withdrawn_templates(workspace) == []

    assert target.is_symlink()
    assert real.read_text(encoding="utf-8") == retired


def test_a_failed_retire_does_not_stop_the_prompt_refresh(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ritirare è un'ottimizzazione; aggiornare i prompt di sistema no.

    Il ritiro gira per primo e scrive con ``atomic_write``, che non ha le difese
    di ``_write_bytes_force`` (nato perché una scrittura fallita al boot manda in
    crash-loop il gateway). Un ``AGENTS.md`` non scrivibile alza da lì, e
    ``runtime/container.py`` — a differenza di ``android_entry.py`` — non
    raccoglie niente: quell'eccezione porterebbe via l'estrazione di ``agent/**``,
    cioè l'unico modo in cui una correzione a un prompt raggiunge un telefono già
    installato.
    """
    (workspace / "AGENTS.md").write_text(
        _retired_fixture("agents_md_retired_v0.3.0.md"), encoding="utf-8"
    )
    prompt = workspace / "agent" / "identity.md"
    original = prompt.read_text(encoding="utf-8")
    prompt.write_text(MARKER, encoding="utf-8")

    def _refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("workspace is read-only")

    monkeypatch.setattr("jenny.utils.android_assets.atomic_write", _refuse)

    sync_workspace_templates(workspace, silent=True)

    assert prompt.read_text(encoding="utf-8") == original


def test_the_retired_digest_registry_has_exactly_one_definition() -> None:
    """Due copie di un insieme che deve restare allineato è il guasto che questo
    repo continua a dover riparare.

    I consumatori sono due — il riconoscimento nel prompt e la riscrittura al
    boot — e stanno in package diversi, che è esattamente la condizione in cui la
    seconda copia nasce. ``session/keys.py``, ``agent/memory.py`` e
    ``agent/autocompact.py`` sono tre copie divergenti della regola sui prefissi
    interni, e ``roadmap/project-sessions.md`` la chiama "a data-loss bug no test
    will catch". Questo è il test che la prende.
    """
    sources = list((Path(__file__).resolve().parents[2] / "jenny").rglob("*.py"))
    assert sources, "nessun sorgente trovato: il path del package è cambiato"

    for name, retired in _RETIRED_TEMPLATE_DIGESTS.items():
        for digest in retired:
            holders = [
                path.name for path in sources if digest in path.read_text(encoding="utf-8")
            ]
            assert holders == ["android_assets.py"], (
                f"il digest ritirato di {name} è scritto in {holders}: "
                "la definizione deve restare una sola"
            )
