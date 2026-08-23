"""I progetti non si archiviano per inattività — e la lunghezza li raggiunge.

Passo **8** di ``roadmap/progetti-passi.md``.

Fino al passo 8 i progetti erano salvi **per accidente**: ``check_expired`` aveva
``UNIFIED_SESSION_KEY`` cablato dentro, quindi guardava una sessione sola e le
altre non le vedeva nemmeno. La riga del piano dice che quel giro *potrà* girare
su tutte le sessioni — e che quando lo farà deve lasciare stare i progetti.
Questo file è il recinto messo prima, così quel giorno non serve ricordarselo.

**Da T6.5 questo file ha una seconda metà: la manopola.** Il recinto non è stato
demolito — ``compact_projects_when_idle`` (spenta di default) lo apre, e ogni test
qui sopra continua a descrivere il comportamento con la manopola spenta. È quel
che rende accendere P4 una prova reversibile invece di una scommessa.

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

# ── L'interruttore di P4 ─────────────────────────────────────────────────
#
# Accendere ``compact_projects_when_idle`` è l'ultimo gradino: da quel momento la
# conversazione di un progetto non è più l'unico depositario di niente — la
# verità sta nelle pagine, che entrano in contesto d'ufficio (T3 e T6.4) — quindi
# archiviarla non butta via nulla.
#
# Nota su cosa si perde comunque: il transcript **visibile** (``.jenny/webui/``)
# non viene toccato dalla compattazione, che riscrive ``sessions/``. L'amnesia è
# dell'agente, non del registro: una persona può ancora rileggere.


@pytest.fixture
def switched_on(tmp_path: Path) -> AutoCompact:
    consolidator = MagicMock()
    consolidator.compact_idle_session = AsyncMock(return_value="riassunto")
    return AutoCompact(
        sessions=SessionManager(tmp_path),
        consolidator=consolidator,
        session_ttl_minutes=30,
        compact_projects=True,
    )


def test_the_switch_opens_the_fence(switched_on: AutoCompact) -> None:
    assert switched_on._may_archive_for_idleness(PROJECT) is True


def test_opening_the_fence_alone_would_do_nothing(switched_on: AutoCompact) -> None:
    """**Il test che conta.** Il recinto e l'elenco dei candidati sono due cose
    diverse: l'elenco ne conteneva **una sola** (la conversazione personale),
    quindi allargare il solo filtro avrebbe lasciato i progetti fuori dal giro
    senza che nulla lo dicesse. La manopola deve fare entrambe le cose.
    """
    _stale(switched_on, PROJECT)

    candidates = switched_on._idle_candidates()

    assert PROJECT in candidates
    assert PERSONAL in candidates


def test_with_the_switch_off_the_project_is_not_even_looked_at(
    autocompact: AutoCompact,
) -> None:
    _stale(autocompact, PROJECT)

    assert autocompact._idle_candidates() == (PERSONAL,)


def test_a_project_with_no_conversation_is_not_a_candidate(switched_on: AutoCompact) -> None:
    """Si guardano i **file di sessione**, non le wiki: un progetto con cui non si
    è mai parlato non ha niente da compattare."""
    assert PROJECT not in switched_on._idle_candidates()


def test_a_project_name_with_an_underscore_survives_the_round_trip(
    switched_on: AutoCompact,
) -> None:
    """``project:mia_wiki`` diventa il file ``project_mia_wiki.jsonl``, e torna
    chiave sostituendo **solo il primo** underscore. Sbagliare qui produce una
    chiave che non corrisponde a nessuna sessione, cioè un progetto che non si
    compatta mai — in silenzio."""
    _stale(switched_on, "project:mia_wiki")

    assert "project:mia_wiki" in switched_on._idle_candidates()


def test_the_transcript_files_are_not_mistaken_for_sessions(
    switched_on: AutoCompact,
) -> None:
    """Accanto a ``project_x.jsonl`` vive ``websocket_project_x.jsonl``, che è
    un'altra cosa. Il glob è ancorato all'inizio del nome, e questo test lo
    tiene tale.

    **Si nega la forma, non una chiave.** La prima stesura negava esattamente
    ``"websocket:project:patreon"`` — e con il glob allargato il transcript entra
    come ``websocket:project_patreon``, che è una chiave *diversa*: l'asserzione
    passava e la mutazione sopravviveva. Quel che va escluso è qualunque
    candidato che non sia una sessione-progetto.
    """
    _stale(switched_on, "websocket:project:patreon")
    _stale(switched_on, PROJECT)

    candidates = switched_on._idle_candidates()

    assert PROJECT in candidates
    assert not [k for k in candidates if "websocket" in k], candidates


@pytest.mark.asyncio
async def test_the_switch_lets_an_idle_project_be_archived(switched_on: AutoCompact) -> None:
    _stale(switched_on, PROJECT)
    scheduled: list[object] = []

    switched_on.check_expired(scheduled.append)

    assert len(scheduled) == 1
    await scheduled[0]
    switched_on.consolidator.compact_idle_session.assert_awaited_once()
    assert switched_on.consolidator.compact_idle_session.await_args[0][0] == PROJECT


@pytest.mark.asyncio
async def test_internal_work_stays_out_even_with_the_switch_on(
    switched_on: AutoCompact,
) -> None:
    """La seconda guardia di ``_archive`` non è ridondante e non è coperta dalla
    manopola: un run di Dream o di Atlas non è una conversazione, e archiviarlo
    gli toglierebbe la coda di lavoro con cui si ricorda dei propri run."""
    _stale(switched_on, "dream:20260823-120000")

    await switched_on._archive("dream:20260823-120000")

    switched_on.consolidator.compact_idle_session.assert_not_awaited()


def test_the_knob_reaches_autocompact_from_the_config() -> None:
    """Il knob è inutile se non arriva: ``AgentDefaults`` →
    ``AgentLoop`` → ``AutoCompact``. Senza questo test la manopola si potrebbe
    accendere in ``config.json`` senza che cambi niente."""
    import inspect

    from jenny.agent.loop import AgentLoop
    from jenny.config.schema import AgentDefaults

    assert AgentDefaults().compact_projects_when_idle is False
    source = inspect.getsource(AgentLoop)
    assert "compact_projects=compact_projects_when_idle" in source
    assert "compact_projects_when_idle=defaults.compact_projects_when_idle" in source
