"""Track file-read state for read-before-edit warnings and read deduplication."""

from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float
    offset: int
    limit: int | None
    content_hash: str | None
    can_dedup: bool


def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def _read_text(p: str) -> str:
    """Contenuto testuale di ``p``, stringa vuota se illeggibile o assente."""
    try:
        return Path(p).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _significant_lines(text: str) -> set[str]:
    """Righe non vuote, normalizzate per confronto."""
    return {line.strip() for line in text.splitlines() if line.strip()}


class FileStates:
    """Per-session read/write tracker.

    Owns its own state dict so read-dedup ("File unchanged since last read")
    and read-before-edit warnings stay scoped to one agent session and do
    not leak across sessions sharing this process.
    """

    __slots__ = (
        "_state",
        "writes_ok",
        "writes_attempted",
        "writes_refused_budget",
        "_refused",
    )

    def __init__(self) -> None:
        self._state: dict[str, ReadState] = {}
        # Contatori di attività di scrittura per la sessione. ``writes_attempted``
        # conta ogni intento di scrittura (anche quelli bloccati da policy o
        # falliti), ``writes_ok`` solo le scritture andate a buon fine. Servono a
        # Dream per distinguere "nulla da scrivere" (avanza il cursore) da
        # "voleva scrivere ma è stato bloccato" (NON avanza) — vedi
        # ``MemoryStore.dream_should_advance_cursor``.
        self.writes_ok: int = 0
        self.writes_attempted: int = 0
        # Rifiuti dovuti al budget dei file di memoria (``write_size_guard``),
        # distinti nei log dai blocchi di policy.
        #
        # Cumulativo, cioè "quante volte il budget ha morso": lo vogliono i log e
        # i test. **Non è ciò che decide il commit** — quello guarda
        # :attr:`unrecovered_refusals`, che è una misura di contenuto ancora
        # mancante e non un conteggio di tentativi.
        #
        # Perché non basta ``attempted``/``ok``: un run che scrive con successo
        # una skill e si vede rifiutare ``MEMORY.md`` arriva a ``ok=1,
        # attempted=2`` e sui soli aggregati passerebbe per riuscito. Il cursore
        # avanzerebbe e il fatto rifiutato non tornerebbe in nessun batch
        # successivo.
        self.writes_refused_budget: int = 0
        # I rifiuti aperti: percorso -> righe che quella scrittura voleva
        # aggiungere e che non sono ancora su disco.
        #
        # Il contatore da solo non distingue due run opposti, e il messaggio di
        # rifiuto stesso produce il secondo: dice al modello *"libera spazio in
        # questo stesso turno: leggi il file, cancella voci, poi scrivi il testo
        # accorciato — una scrittura che rimpicciolisce è sempre accettata"*. Un
        # modello che obbedisce alla lettera mette il fatto su disco e riporta il
        # file sotto il tetto, cioè fa esattamente il lavoro; con il solo
        # contatore quel run restava "rifiutato", il cursore non avanzava, lo
        # stesso batch tornava due ore dopo, ``stuck`` saliva e l'allarme sul
        # telefono annunciava scritture rifiutate che erano riuscite.
        #
        # Ma il solo percorso non basta, e questa è la correzione del 2026-08-17:
        # il guard accetta una scrittura se rientra nel tetto **oppure** se
        # rimpicciolisce il file, e i due rami finiscono entrambi in
        # :meth:`record_write`. Quindi "ha riscritto il file portandosi dentro il
        # fatto" e "ha potato il file e ha buttato il fatto" sono lo stesso
        # evento per un insieme di soli percorsi — e il secondo è il più
        # probabile, perché il messaggio di rifiuto spinge il modello a
        # riscrivere proprio quel file e chiude dicendo che se il fatto nuovo
        # vale, qualcosa che c'è già non vale.
        #
        # Quel che serve sapere è se il contenuto rifiutato è **atterrato**, non
        # se il file è stato toccato di nuovo. Si tengono quindi le righe che la
        # scrittura rifiutata stava aggiungendo, e il rifiuto si chiude quando una
        # scrittura riuscita su quel percorso ne fa atterrare almeno una.
        #
        # Due dettagli che sembrano cavilli e non lo sono, misurati il 2026-08-17.
        #
        # *Si accumula, non si sovrascrive.* Due rifiuti sullo stesso file in un
        # turno — un run che instrada due fatti in ``MEMORY.md`` — e la seconda
        # assegnazione cancellava la prima: bastava far atterrare il secondo fatto
        # perché il run commettesse con il primo perso in silenzio, cioè
        # esattamente la perdita che questo meccanismo esiste per impedire.
        #
        # *Basta una riga, non tutte.* Il testo rifiutato è il contenuto
        # **intero** del file, quindi le sue "righe aggiunte" comprendono ogni
        # riga riformulata, non solo il fatto nuovo — e ``dream_review.md``
        # autorizza esplicitamente quel run a riformulare. Pretendere che
        # atterrino tutte rimetteva il livelock proprio sui run che avevano
        # lavorato di più. Il prezzo che resta: una scrittura rifiutata che
        # aggiungeva *due* fatti si chiude quando ne atterra uno. È più stretto
        # del prezzo che paga: là si bloccava un run corretto, qui si perde metà
        # di un caso più raro.
        self._refused: dict[str, frozenset[str]] = {}

    @property
    def unrecovered_refusals(self) -> int:
        """Quante scritture rifiutate dal budget non sono mai atterrate.

        È la lettura che governa il commit di un run interno: > 0 significa che
        un contenuto voluto non è su disco e la history che lo conteneva va
        riprocessata. Zero non significa "nessun rifiuto" — significa "nessun
        rifiuto rimasto aperto".
        """
        return len(self._refused)

    def record_write_refused(self, path: str | Path, text: str = "") -> None:
        """Registra un rifiuto del budget su ``path`` per il contenuto ``text``.

        Alza il contatore cumulativo e apre il rifiuto, memorizzando le righe che
        ``text`` aggiungeva rispetto a ciò che è su disco adesso. Il rifiuto si
        chiude quando una scrittura riuscita su questo percorso ne fa atterrare
        almeno una (:meth:`record_write`).

        Un secondo rifiuto sullo stesso percorso **si somma** al primo: due fatti
        instradati nello stesso file sono due cose da non perdere, e sovrascrivere
        avrebbe dimenticato la prima.

        ``text`` vuoto — o una scrittura che non aggiungeva nessuna riga nuova,
        per esempio un riordino a pari dimensione — non dà niente da cercare: in
        quel caso il rifiuto si chiude alla prima scrittura riuscita, che è il
        comportamento conservativo di prima. Non c'è un fatto identificabile da
        perdere.
        """
        self.writes_refused_budget += 1
        p = str(Path(path).resolve())
        added = _significant_lines(text) - _significant_lines(_read_text(p))
        previous = self._refused.get(p)
        self._refused[p] = frozenset(added) if previous is None else previous | added

    def record_write_attempt(self) -> None:
        """Registra un intento di scrittura, prima della risoluzione del path.

        Chiamato all'inizio di ``_FsTool._resolve_write`` così da contare anche
        gli intenti bloccati (``PermissionError``) o falliti, non solo quelli
        riusciti tracciati da :meth:`record_write`.
        """
        self.writes_attempted += 1

    def record_read(self, path: str | Path, offset: int = 1, limit: int | None = None) -> None:
        """Record that a file was read (called after successful read)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=offset,
            limit=limit,
            content_hash=_hash_file(p),
            can_dedup=True,
        )

    def record_write(self, path: str | Path) -> None:
        """Record that a file was written (updates mtime in state)."""
        # Chiamato solo dopo una scrittura riuscita: conta come esito positivo.
        self.writes_ok += 1
        p = str(Path(path).resolve())
        # E chiude un eventuale rifiuto su *questo* file, ma solo se il contenuto
        # rifiutato è davvero atterrato. Il testo si rilegge da disco invece di
        # farsi passare dal chiamante: la scrittura è appena avvenuta, quindi il
        # file *è* la verità, e non c'è nessuna firma da cambiare nei sei punti
        # che chiamano questo metodo.
        pending = self._refused.get(p)
        if pending is not None and (not pending or pending & _significant_lines(_read_text(p))):
            del self._refused[p]
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._state.pop(p, None)
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=1,
            limit=None,
            content_hash=_hash_file(p),
            can_dedup=False,
        )

    def check_read(self, path: str | Path) -> str | None:
        """Check if a file has been read and is fresh.

        Returns None if OK, or a warning string.
        When mtime changed but file content is identical (e.g. touch, editor save),
        the check passes to avoid false-positive staleness warnings.
        """
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return "Warning: file has not been read yet. Read it first to verify content before editing."
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return None
        if current_mtime != entry.mtime:
            if entry.content_hash and _hash_file(p) == entry.content_hash:
                entry.mtime = current_mtime
                return None
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        # mtime unchanged - still check content hash to detect quick modifications
        if entry.content_hash and _hash_file(p) != entry.content_hash:
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        return None

    def get(self, path: str | Path) -> ReadState | None:
        """Return the raw ReadState entry for a path, or None."""
        return self._state.get(str(Path(path).resolve()))

    def clear(self) -> None:
        """Clear all tracked state (useful for testing)."""
        self._state.clear()
        self.writes_ok = 0
        self.writes_attempted = 0
        self.writes_refused_budget = 0
        self._refused.clear()


class FileStateStore:
    """Lookup table for per-session file read/write state."""

    __slots__ = ("_states_by_key",)

    def __init__(self) -> None:
        self._states_by_key: dict[str, FileStates] = {}

    def for_session(self, session_key: str | None) -> FileStates:
        key = session_key or "__default__"
        states = self._states_by_key.get(key)
        if states is None:
            states = FileStates()
            self._states_by_key[key] = states
        return states

    def drop(self, session_key: str | None) -> None:
        """Dimentica quel che *session_key* ha letto.

        Serve a chi azzera una conversazione. Il dedup delle letture poggia su
        un presupposto — «il contenuto di questo file e' gia' nel contesto» — che
        vale finche' i messaggi che lo portavano esistono; svuotata la
        conversazione, quel presupposto e' falso e lo stub risponderebbe «invariato
        dall'ultima lettura» a un interlocutore che non l'ha mai letto (visto sul
        telefono il 23/08).
        """
        self._states_by_key.pop(session_key or "__default__", None)

    def clear(self) -> None:
        self._states_by_key.clear()


_current_file_states: ContextVar[FileStates | None] = ContextVar(
    "jenny_file_states",
    default=None,
)


def current_file_states(default: FileStates) -> FileStates:
    """Return the FileStates bound to the current agent task, or a fallback."""
    return _current_file_states.get() or default


def bind_file_states(file_states: FileStates) -> Token[FileStates | None]:
    """Bind file read/write state for the current async task."""
    return _current_file_states.set(file_states)


def reset_file_states(token: Token[FileStates | None]) -> None:
    _current_file_states.reset(token)
