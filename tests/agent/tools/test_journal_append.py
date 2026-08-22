"""``journal_append``: la cattura di un progetto, e i suoi tre cancelli.

Passo **T2.5** di ``roadmap/taccuino-passi.md``. Il tool nasce da una misura sul
telefono, non da un disegno: la politica di cattura funzionava, ma passava da uno
spawn di subagent, perché ``orchestrator_mode`` toglie all'agente principale ogni
scrittura. Una corsa di subagent per una riga di testo, a ogni turno con un fatto
dentro.

I test che contano più degli altri sono due, e sono quelli sull'**append-only**:
il diario è l'ingresso del giardiniere (T4), che lo legge assumendolo immobile. Se
questo tool potesse riscrivere una riga, quel presupposto cadrebbe qui — e
cadrebbe in silenzio, perché nessuno rilegge il diario di ieri.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from jenny.agent.tools.journal import JournalAppendTool
from jenny.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)

_DAY = date(2026, 8, 22)


def _wiki(root: Path, name: str = "viaggio") -> Path:
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True)
    (project / "raw" / "journal").mkdir(parents=True)
    return project


def _bind(project: Path | None, *, writable: bool = True):
    """Lega uno scope di turno. ``project=None`` = nessun progetto."""
    if project is None:
        return None
    scope = WorkspaceScope(
        project_path=project,
        access_mode="restricted",
        restrict_to_workspace=True,
        sandbox_status=workspace_sandbox_status(
            restrict_to_workspace=True, workspace=project
        ),
        writable=writable,
    )
    return bind_workspace_scope(scope)


@pytest.fixture
def tool() -> JournalAppendTool:
    return JournalAppendTool(today=lambda: _DAY)


def _page(project: Path) -> Path:
    return project / "raw" / "journal" / "20260822.md"


# ── Quel che scrive ──────────────────────────────────────────────────────────


async def test_the_first_line_creates_the_page_with_its_heading(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="il furgone ha le gomme da cambiare")
    finally:
        reset_workspace_scope(token)

    text = _page(project).read_text(encoding="utf-8")
    assert text.startswith("# 2026-08-22\n\n")
    assert "— il furgone ha le gomme da cambiare\n" in text
    assert "journal/20260822.md" in out


async def test_the_timestamp_is_added_and_the_text_is_not(tool, tmp_path) -> None:
    """L'ora la mette il codice: chiedendola al modello si otterrebbe un orario
    plausibile invece di quello vero, che è il tipo di dato che non si nota mai
    sbagliato."""
    import re

    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="12:00 non è l'ora, è il testo")
    finally:
        reset_workspace_scope(token)

    line = _page(project).read_text(encoding="utf-8").strip().splitlines()[-1]
    assert re.fullmatch(r"- \d{2}:\d{2} — 12:00 non è l'ora, è il testo", line), line


async def test_a_multiline_text_becomes_one_line(tool, tmp_path) -> None:
    """Il formato del diario è una riga per fatto, e la riga la fa il tool: un
    a-capo passato dal modello spezzerebbe il file, e il giardiniere leggerebbe
    due mezzi fatti."""
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="prima parte\n\nseconda   parte\n")
    finally:
        reset_workspace_scope(token)

    body = _page(project).read_text(encoding="utf-8")
    entries = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert len(entries) == 1, f"un testo su più righe deve fare una voce, non {len(entries)}"
    assert body.strip().endswith("— prima parte seconda parte")


# ── L'append-only ────────────────────────────────────────────────────────────


async def test_a_second_call_appends_and_leaves_the_first_line_alone(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        await tool.execute(text="primo fatto")
        first = _page(project).read_bytes()
        await tool.execute(text="secondo fatto")
    finally:
        reset_workspace_scope(token)

    after = _page(project).read_bytes()
    assert after.startswith(first), "la coda è cresciuta ma la testa deve essere identica"
    lines = [ln for ln in after.decode().splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    assert "primo fatto" in lines[0] and "secondo fatto" in lines[1]


async def test_it_does_not_touch_a_page_written_by_someone_else(tool, tmp_path) -> None:
    """Su un diario che esiste già — scritto a mano, o dal giorno prima — non c'è
    nessuna riscrittura: si apre in coda e basta. È la proprietà su cui poggia
    il giardiniere, e il modo più facile di perderla era un leggi-modifica-scrivi.
    """
    project = _wiki(tmp_path)
    _page(project).write_text("# 2026-08-22\n\n- 09:00 — scritto a mano\n", encoding="utf-8")
    token = _bind(project)
    try:
        await tool.execute(text="aggiunto dal tool")
    finally:
        reset_workspace_scope(token)

    text = _page(project).read_text(encoding="utf-8")
    assert text.startswith("# 2026-08-22\n\n- 09:00 — scritto a mano\n")
    assert text.count("# 2026-08-22") == 1, "l'intestazione non si riscrive"


# ── I cancelli ───────────────────────────────────────────────────────────────


async def test_no_project_means_no_journal_and_it_says_where_one_lives(tool, tmp_path) -> None:
    """Fuori da un progetto la cattura non ha una cartella dove andare, e il
    rifiuto **dice dove** invece di dire solo che qui non si può — la lezione del
    passo 6: un rifiuto che manda via a mani vuote costa un altro turno."""
    plain = tmp_path / "workspace"
    plain.mkdir()
    token = _bind(plain)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "No journal here" in out
    assert "chip above the composer" in out
    assert not list(plain.rglob("*.md"))


async def test_with_no_bound_scope_it_refuses_too(tool) -> None:
    """Nessuno scope legato vuol dire "fuori da un turno": test, ispezione,
    sessioni interne. Non c'è un progetto, quindi non c'è un diario — e il
    default deve essere il rifiuto, non una cartella indovinata."""
    assert "No journal here" in await tool.execute(text="un fatto")


async def test_read_only_refuses_and_writes_nothing(tool, tmp_path) -> None:
    project = _wiki(tmp_path)
    token = _bind(project, writable=False)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "read-only" in out.lower()
    assert not _page(project).exists()


async def test_the_missing_project_wins_over_read_only(tool, tmp_path) -> None:
    """L'ordine dei due rifiuti non è indifferente: dire «sola lettura» a chi non
    ha nemmeno una cartella dove scrivere lo manda a cercare l'interruttore per
    un problema che l'interruttore non risolve."""
    plain = tmp_path / "workspace"
    plain.mkdir()
    token = _bind(plain, writable=False)
    try:
        out = await tool.execute(text="un fatto")
    finally:
        reset_workspace_scope(token)

    assert "No journal here" in out


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
async def test_an_empty_text_writes_nothing(tool, tmp_path, text) -> None:
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text=text)
    finally:
        reset_workspace_scope(token)

    assert "Nothing to append" in out
    assert not _page(project).exists()


async def test_page_material_is_refused_instead_of_truncated(tool, tmp_path) -> None:
    """Un fatto sta in una riga. Quel che non ci sta è materiale da pagina, e il
    posto di una pagina non lo decide la cattura: troncare in silenzio
    scriverebbe metà fatto e lo farebbe sembrare intero."""
    project = _wiki(tmp_path)
    token = _bind(project)
    try:
        out = await tool.execute(text="x" * 501)
    finally:
        reset_workspace_scope(token)

    assert "Too long" in out and "page material" in out
    assert not _page(project).exists()


# ── Il perché esiste ─────────────────────────────────────────────────────────


def test_it_is_reachable_by_the_orchestrator() -> None:
    """È la ragione del tool. L'agente principale gira con
    ``orchestrator_mode`` acceso — niente ``python_exec``, niente scrittura,
    niente patch — quindi senza questo tool la cattura è per forza uno spawn: una
    corsa di subagent per una riga di testo, a ogni turno con un fatto dentro.
    Misurato sul telefono il 22/08, ed è la sola ragione per cui una scrittura
    entra in uno scope fatto di sola lettura e controllo.
    """
    assert "orchestrator" in JournalAppendTool._scopes


def test_it_is_registered_in_the_loader() -> None:
    """La registrazione è esplicita (``TOOLS`` + la lista in ``loader.py``): un
    tool che esiste e non è in lista è un tool che nessuno chiamerà mai, e non
    fallisce — semplicemente non c'è."""
    from jenny.agent.tools import loader

    src = Path(loader.__file__).read_text(encoding="utf-8")
    assert '"journal",' in src

    from jenny.agent.tools.journal import TOOLS

    assert TOOLS == [JournalAppendTool]
