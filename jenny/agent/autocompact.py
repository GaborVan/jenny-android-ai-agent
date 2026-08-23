"""Auto compact: proactive compression of idle sessions to reduce token cost and latency."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Coroutine

from loguru import logger

from jenny.session.keys import (
    ATLAS_SESSION_PREFIX,
    DREAM_SESSION_PREFIX,
    PROJECT_SESSION_PREFIX,
    UNIFIED_SESSION_KEY,
    is_project_session_key,
)
from jenny.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from jenny.agent.memory import Consolidator


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8
    # Sottoinsieme *deliberatamente stretto* del vocabolario di
    # :mod:`jenny.session.keys`: qui "interna" non vuol dire "non e' l'utente",
    # vuol dire "non deve avere ne archiviazione per inattivita ne reiniezione
    # di ``_last_summary``". Allargarlo a ``cron:``/``heartbeat``/``internal:``/
    # ``subagent:`` toglierebbe a quelle sessioni il summary che oggi
    # ``prepare_session`` ricarica quando superano il budget di token: sarebbe
    # una regressione, non un allineamento. Se l'insieme debba coincidere con
    # ``is_internal_session_key`` e' una decisione aperta, da prendere a parte.
    # I prefissi arrivano comunque dalle costanti condivise, cosi la *forma*
    # della chiave non puo divergere dal lato che la scrive.
    _INTERNAL_SESSION_PREFIXES = (DREAM_SESSION_PREFIX, ATLAS_SESSION_PREFIX)

    # Le sessioni che il giro per inattivita' prende in considerazione, **prima**
    # del recinto di :meth:`_may_archive_for_idleness`. Oggi una sola.
    #
    # E' un attributo e non una costante dentro ``_idle_candidates`` per una
    # ragione precisa: cosi' un test puo' allargare l'*elenco* lasciando in
    # piedi il filtro vero. Con l'elenco cablato nel metodo, l'unico modo di
    # provare il filtro era sovrascrivere il metodo — e un test che
    # sovrascrive il metodo prova la propria copia, non il codice (misurato per
    # mutazione il 22/08: togliere il filtro non faceva cadere niente).
    #
    # E' anche il punto che una generalizzazione allargherebbe.
    _IDLE_CANDIDATE_KEYS: tuple[str, ...] = (UNIFIED_SESSION_KEY,)

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0,
                 compact_projects: bool = False):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes
        # L'interruttore di P4 (``config.agents.defaults.compact_projects_when_idle``).
        # Spento, il recinto sotto vale come prima; acceso, la conversazione di un
        # progetto si compatta come quella personale — perche' la verita' non sta
        # piu' li', sta nelle pagine.
        self._compact_projects = compact_projects
        self._archiving: set[str] = set()
        self._summaries: dict[str, tuple[str, datetime]] = {}

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        return f"Previous conversation summary (last active {last_active.isoformat()}):\n{text}"

    @classmethod
    def _is_internal_session(cls, key: str) -> bool:
        return key.startswith(cls._INTERNAL_SESSION_PREFIXES)

    def _may_archive_for_idleness(self, key: str) -> bool:
        """Se questa sessione puo' essere archiviata perche' e' stata zitta.

        La regola e' **«non un progetto»**, e la classificazione arriva dal
        predicato canonico di :mod:`jenny.session.keys`: la tassonomia delle
        sessioni vive la' e non si riscrive il confronto qui.

        Perche' i progetti no: archiviare per *tempo passato* la storia lunga di
        un argomento butta esattamente la cosa che rende un progetto utile. Un
        progetto puo' stare fermo tre settimane e riprendere dove era: e' il suo
        mestiere. Quel che non perde e' la compattazione per **lunghezza**, che
        gira a ogni turno di ogni sessione
        (``Consolidator.maybe_consolidate_by_tokens``, v. ``loop.py``): il
        recinto e' sul tempo, non sulla dimensione.

        **E perche' non una whitelist**, che sarebbe la forma piu' prudente e che
        qui e' stata provata e scartata. Due ragioni. La prima: una whitelist
        "solo la personale" bloccherebbe la generalizzazione per cui questo passo
        esiste — la riga del piano dice che l'archiviazione *puo'* girare su tutte
        le sessioni, purche' non trascini il lavoro interno nel diario e lasci
        stare i progetti. La seconda: la prudenza che una whitelist compra qui
        non c'e' comunque, perche' :func:`jenny.session.keys.session_kind` chiude
        la tassonomia a tre etichette e fa cadere **su "personal"** tutto quel
        che non riconosce. Una quarta forma di chiave risulterebbe personale in
        entrambe le forme; fingere il contrario sarebbe una falsa sicurezza.

        Fino al passo 8 questa regola non era scritta: :meth:`check_expired`
        aveva ``UNIFIED_SESSION_KEY`` cablato dentro, quindi i progetti erano
        salvi **per accidente** e non per decisione. Sta qui e non nel chiamante
        perche' chi generalizzera' questa funzione toccherebbe lei.

        **E da T6.5 c'e' una manopola** (``compact_projects_when_idle``, spenta di
        default). Accenderla e' l'ultimo gradino di P4: da quel momento la
        conversazione di un progetto non e' piu' l'unico depositario di niente,
        quindi archiviarla non butta via nulla — la verita' sta nelle pagine, che
        entrano in contesto d'ufficio (T3 e T6.4). Il recinto resta la posizione
        di partenza, e resta reversibile: e' quel che rende accendere P4 una prova
        invece di una scommessa.
        """
        if self._compact_projects:
            return True
        return not is_project_session_key(key)

    def _idle_candidates(self) -> tuple[str, ...]:
        """Le sessioni che questo giro puo' guardare.

        Oggi una sola — la conversazione personale — ma passa dal filtro invece
        di essere una costante, e la differenza non e' cosmetica: **una guardia
        che non puo' scattare non e' provabile**, e una guardia non provabile
        non e' una guardia. Prima questa funzione aveva
        ``UNIFIED_SESSION_KEY`` cablato e un ``if`` subito sotto: togliere
        quell'``if`` non faceva cadere nessun test, perche' la sola chiave che ci
        arrivava era comunque ammessa (misurato per mutazione il 22/08).

        E' anche la riga che una generalizzazione allargherebbe: estendere
        l'elenco qui fa passare le sessioni nuove dal recinto senza doverselo
        ricordare.
        """
        keys = list(self._IDLE_CANDIDATE_KEYS)
        if self._compact_projects:
            # **Aprire il recinto non basta.** L'elenco dei candidati contiene
            # una chiave sola, quindi con il solo filtro allargato nessun
            # progetto verrebbe mai *guardato*: il recinto e la lista sono due
            # cose diverse, e la seconda e' quella che decide chi entra nel giro.
            keys.extend(self._project_session_keys())
        return tuple(key for key in keys if self._may_archive_for_idleness(key))

    def _project_session_keys(self) -> tuple[str, ...]:
        """Le sessioni-progetto che esistono su disco.

        Si guardano i **file**, non le wiki: un progetto senza conversazione non
        ha niente da compattare, e un progetto la cui cartella e' stata rinominata
        conserva la propria sessione (passo 7). Lo stesso mestiere che
        ``MemoryStore.prune_internal_sessions`` fa per i run interni, con la
        stessa traduzione nome-file -> chiave.
        """
        directory = getattr(self.sessions, "sessions_dir", None)
        if directory is None:
            return ()
        prefix = PROJECT_SESSION_PREFIX[:-1]  # "project", senza i due punti
        try:
            files = sorted(directory.glob(f"{prefix}_*.jsonl"))
        except OSError:
            return ()
        return tuple(path.stem.replace("_", ":", 1) for path in files)

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """Schedule archival of idle sessions, unless a task is in flight."""
        for key in self._idle_candidates():
            if key in self._archiving or key in active_session_keys:
                continue
            info = self.sessions.read_session_metadata(key)
            if info is None:
                continue
            if self._is_expired(info.get("updated_at")):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        # Secondo controllo, e non e' ridondante: ``_archive`` e' una coroutine
        # che qualcuno pianifica, quindi e' raggiungibile senza passare da
        # ``check_expired`` — ed e' l'ultimo punto prima di riscrivere la
        # sessione. Il primo guardia l'ingresso, questo la scrittura.
        if not self._may_archive_for_idleness(key) or self._is_internal_session(key):
            self._archiving.discard(key)
            return
        try:
            summary = await self.consolidator.compact_idle_session(
                key, self._RECENT_SUFFIX_MESSAGES,
            )
            if summary and summary != "(nothing)":
                session = self.sessions.get_or_create(key)
                meta = session.metadata.get("_last_summary")
                if isinstance(meta, dict):
                    self._summaries[key] = (
                        meta["text"],
                        datetime.fromisoformat(meta["last_active"]),
                    )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        if self._is_internal_session(key):
            self._archiving.discard(key)
            self._summaries.pop(key, None)
            return session, None
        if key in self._archiving or self._is_expired(session.updated_at):
            # Il log sta *dentro* il confronto e non prima della chiamata: se la
            # sessione e' gia in ``SessionManager._cache`` — il caso normale, ed
            # e' l'unico che l'heartbeat incontra a ogni giro — ``get_or_create``
            # restituisce lo stesso oggetto e non ha ricaricato niente. Loggare
            # "reloading" li faceva credere a un ricaricamento a ogni battito.
            reloaded = self.sessions.get_or_create(key)
            if reloaded is not session:
                logger.info(
                    "Auto-compact: reloaded session {} (archiving={})",
                    key, key in self._archiving,
                )
            session = reloaded
        # Hot path: summary from in-memory dict (process hasn't restarted).
        entry = self._summaries.pop(key, None)
        if entry:
            return session, self._format_summary(entry[0], entry[1])
        # Cold path: summary persisted in session metadata (process restarted).
        meta = session.metadata.get("_last_summary")
        if isinstance(meta, dict):
            return session, self._format_summary(meta["text"], datetime.fromisoformat(meta["last_active"]))
        return session, None
