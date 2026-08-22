"""I progetti non si archiviano per inattività — e la lunghezza li raggiunge.

Passo **8** di ``roadmap/progetti-passi.md``.

Fino al passo 8 i progetti erano salvi **per accidente**: ``check_expired`` aveva
``UNIFIED_SESSION_KEY`` cablato dentro, quindi guardava una sessione sola e le
altre non le vedeva nemmeno. La riga del piano dice che quel giro *potrà* girare
su tutte le sessioni — e che quando lo farà deve lasciare stare i progetti.
Questo file è il recinto messo prima, così quel giorno non serve ricordarselo.

**Le due metà vanno lette insieme.** Da sola, la prima si legge come «i progetti
non si compattano», che è falso e sarebbe una brutta sorpresa al primo progetto
da duecento turni: la compattazione per **lunghezza** li raggiunge come tutti, a
ogni turno. Il recinto è sul tempo passato, non sulla dimensione.

Perché archiviare per tempo un progetto è sbagliato, e non solo diverso: un
progetto può stare fermo tre settimane e riprendere dove era — è il suo mestiere.
Comprimerlo perché è stato zitto butta la sola cosa che una sessione di progetto
ha in più della sua cartella.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenny.agent.autocompact import AutoCompact
from jenny.session.manager import SessionManager

PROJECT = "project:patreon"
PERSONAL = "unified:default"


@pytest.fixture
def autocompact(tmp_path: Path) -> AutoCompact:
    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="riassunto")
    return AutoCompact(
        sessions=SessionManager(tmp_path), consolidator=consolidator, session_ttl_minutes=30
    )


def _stale(autocompact: AutoCompact, key: str) -> None:
    """Una sessione ferma da molto più del TTL, salvata su disco."""
    session = autocompact.sessions.get_or_create(key)
    session.messages.append({"role": "user", "content": "ciao"})
    session.updated_at = datetime.now() - timedelta(hours=6)
    autocompact.sessions.save(session)


# ── Il recinto ───────────────────────────────────────────────────────────


def test_a_project_is_never_archived_for_idleness(autocompact: AutoCompact) -> None:
    assert autocompact._may_archive_for_idleness(PROJECT) is False


@pytest.mark.parametrize(
    "key",
    [PERSONAL, "websocket:default", "cron:update_check", "dream:20260822-120000"],
    ids=["personale", "websocket", "cron", "dream"],
)
def test_everything_else_may_still_be(autocompact: AutoCompact, key: str) -> None:
    """La regola è «non un progetto», non «solo la personale».

    Una whitelist stretta bloccherebbe la generalizzazione per cui il passo 8
    esiste: la riga del piano dice che quel giro *può* girare su tutte le
    sessioni, purché non trascini il lavoro interno nel diario.
    """
    assert autocompact._may_archive_for_idleness(key) is True


async def test_the_write_is_guarded_too_not_just_the_entry(autocompact: AutoCompact) -> None:
    """``_archive`` è una coroutine che qualcuno pianifica: è raggiungibile da sola.

    Il controllo in ``check_expired`` protegge l'ingresso; questo protegge la
    riscrittura della sessione, che è l'ultimo istante utile.
    """
    _stale(autocompact, PROJECT)
    await autocompact._archive(PROJECT)
    autocompact.consolidator.compact_idle_session.assert_not_awaited()


async def test_and_the_personal_one_does_get_archived(autocompact: AutoCompact) -> None:
    """Controprova: se non passasse nemmeno questa, il recinto sarebbe un muro."""
    _stale(autocompact, PERSONAL)
    await autocompact._archive(PERSONAL)
    autocompact.consolidator.compact_idle_session.assert_awaited_once()


def test_the_candidate_list_filters_a_project_out(autocompact: AutoCompact) -> None:
    """La guardia all'ingresso è sull'elenco dei candidati, e per questo si prova.

    Con ``UNIFIED_SESSION_KEY`` cablato e un ``if`` sotto, togliere quell'``if``
    non faceva cadere niente: la sola chiave che ci arrivava era comunque
    ammessa. Qui invece l'elenco si può allargare — che è anche quel che farà la
    generalizzazione — e il filtro si vede lavorare.
    """
    # Si allarga l'**elenco**, non il metodo: sovrascrivere ``_idle_candidates``
    # proverebbe la copia scritta qui e non il filtro vero — è così che la prima
    # versione di questo test passava anche col filtro rimosso.
    autocompact._IDLE_CANDIDATE_KEYS = (PERSONAL, PROJECT)
    assert autocompact._idle_candidates() == (PERSONAL,)


def test_a_stale_project_is_never_scheduled_even_if_listed(
    autocompact: AutoCompact,
) -> None:
    """E la stessa porta, guardata dal lato della pianificazione."""
    _stale(autocompact, PROJECT)
    _stale(autocompact, PERSONAL)

    autocompact._IDLE_CANDIDATE_KEYS = (PERSONAL, PROJECT)
    scheduled: list = []
    autocompact.check_expired(scheduled.append)
    assert len(scheduled) == 1, "solo la personale, anche con il progetto in elenco"
    for coro in scheduled:
        coro.close()


# ── La controprova: la lunghezza li raggiunge ────────────────────────────


def test_length_based_compaction_runs_for_every_session_including_projects() -> None:
    """Il recinto toglie il tempo, non la dimensione.

    ``maybe_consolidate_by_tokens`` è chiamata sul percorso di *ogni* turno, senza
    guardare la chiave: è quello che impedisce a una sessione di progetto di
    crescere per sempre ora che l'archiviazione per inattività non la tocca. Se
    quella chiamata diventasse condizionale sulla chiave, questo test è il posto
    in cui accorgersene.
    """
    src = Path("jenny/agent/loop.py").read_text(encoding="utf-8")
    # **Dopo** `prepare_session`, non la prima del file: la prima occorrenza sta
    # in `_on_context_overflow`, cioè *prima* nel testo, e cercarla da lì dava una
    # finestra vuota — un test che passava qualunque cosa ci si mettesse dentro
    # (scoperto per mutazione il 22/08).
    start = src.index("self.auto_compact.prepare_session(session, key)")
    call = src.index("await self.consolidator.maybe_consolidate_by_tokens(", start)
    assert call > start
    # Nessun ramo sulla chiave fra le due: la finestra è corta apposta, ed è dove
    # un filtro verrebbe aggiunto.
    window = src[start:call]
    for sospetto in ("is_project_session_key", "project:", "session_kind"):
        assert sospetto not in window, (
            f"la compattazione per lunghezza è diventata condizionale ({sospetto}): "
            "i progetti non hanno più niente che li contenga"
        )
