"""Memory system: pure file I/O store and lightweight Consolidator."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from loguru import logger

from jenny.agent.memory_archive import archive_dir
from jenny.session.keys import DREAM_SESSION_PREFIX, is_internal_session_key
from jenny.utils.helpers import (
    ensure_dir,
    strip_think,
    truncate_text,
    truncate_text_to_tokens,
)
from jenny.utils.path import atomic_write
from jenny.utils.prompt_templates import render_template

# Separatore fra il template di Dream e il batch di storia, dentro il prompt che
# ``MemoryStore.build_dream_prompt`` incolla. È una costante perché lo legge anche
# ``dream_prompt_history``, che sul prompt fa il taglio inverso.
DREAM_HISTORY_HEADER = "\n\n## Conversation History\n"

# Il blocco "già registrato" che la fase 4 del piano aggiunge al prompt del
# Consolidator. Tre numeri, e nessuno è arbitrario:
#
# ``_KNOWN_FACTS_MAX_TOKENS`` è **misurato, non stimato**. Era 1.200, scelto
# sommando i tetti dei due file caldi; sul Titan 2 il blocco reale è uscito a
# 5.239 caratteri contro i 4.800 concessi, perché quel conto dimenticava le
# istruzioni in testa e la coda in attesa. 1.600 li copre con margine, e resta
# comunque irrilevante contro la finestra da cui viene sottratto (v.
# ``Consolidator._truncate_to_token_budget``). Il taglio non è lì per il costo:
# è lì per il giorno in cui i tetti dei file si alzano senza che nessuno guardi
# qui.
#
# ``_KNOWN_FACTS_PENDING_ENTRIES`` limita quante voci di history si leggono, e
# ``_KNOWN_FACTS_PENDING_SHARE`` quanto blocco possono occupare. Servono i due
# insieme e per ragioni opposte: la coda è la fonte dominante del difetto — una
# conversazione lunga viene consolidata più volte prima che Dream giri una sola
# — quindi le va garantito uno spazio; ma è anche l'unica delle due sorgenti
# senza un tetto proprio, e un Dream fermo da giorni (cioè esattamente il
# guasto per cui esiste questo piano) le farebbe altrimenti prendere il blocco
# intero, cancellando i file e facendo riestrarre tutto USER.md. La quota è un
# pavimento per la coda, non un soffitto per il resto: ciò che non spende torna
# ai file.
_KNOWN_FACTS_MAX_TOKENS = 1600
_KNOWN_FACTS_PENDING_ENTRIES = 20
_KNOWN_FACTS_PENDING_SHARE = 0.4

# I mark che il Consolidator scrive davanti a ogni fatto. Elencati qui e non
# dedotti da una regex generica perché questa lista è anche il filtro che tiene
# fuori dal blocco i raw-dump: quando la chiamata LLM fallisce, in history
# finisce una conversazione intera sotto ``[RAW]``, e reiniettarla nel prompt
# della consolidation successiva sarebbe rimettere in circolo esattamente ciò
# che la consolidation esiste per togliere.
_FACT_LINE = re.compile(
    r"^[-*][ \t]+\[(permanent|durable|ephemeral|correction|skip)\][ \t]*(\S.*)$"
)


def _entries_cost(entries: list[str]) -> int:
    return sum(len(entry) + 1 for entry in entries)


def _pack_entries(entries: list[str], budget_chars: int | None) -> tuple[list[str], int]:
    """Le voci che entrano nel budget, intere, e quante ne restano fuori.

    Il taglio a carattere di ``truncate_text_to_tokens`` qui non va bene: mezza
    voce in un elenco di fatti già registrati si legge come un fatto diverso, e
    il modello confronterebbe con qualcosa che nessuno ha mai scritto.
    """
    if budget_chars is None:
        return list(entries), 0
    kept: list[str] = []
    used = 0
    for entry in entries:
        cost = len(entry) + 1
        if used + cost > budget_chars:
            break
        kept.append(entry)
        used += cost
    return kept, len(entries) - len(kept)


def iter_fact_lines(text: str) -> list[tuple[str, str]]:
    """I fatti annotati dentro una voce di history, come ``(mark, fatto)``.

    Serve a due chiamanti con lo stesso bisogno da lati opposti: il blocco
    "già registrato" li legge per dire cosa è in attesa, e la misura della
    fase 4 li conta per dire quanti ne sono usciti da un run.
    """
    found: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        match = _FACT_LINE.match(line.strip())
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# MemoryStore — pure file I/O layer
# ---------------------------------------------------------------------------

class MemoryStore:
    """Pure file I/O for memory files: MEMORY.md, history.jsonl, SOUL.md, USER.md."""

    _DEFAULT_MAX_HISTORY = 1000

    def __init__(self, workspace: Path, max_history_entries: int = _DEFAULT_MAX_HISTORY):
        self.workspace = workspace
        self.max_history_entries = max_history_entries
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        # Rubrica compilata da Atlas a partire da workspace/wikis/. Vive qui
        # accanto a MEMORY.md perché è memoria a tutti gli effetti, ma è un file
        # distinto con un proprietario distinto: Dream non ha il permesso di
        # scriverlo e Atlas non ha il permesso di scrivere MEMORY.md.
        self.wiki_file = self.memory_dir / "WIKI.md"
        self.history_file = self.memory_dir / "history.jsonl"
        self.soul_file = workspace / "SOUL.md"
        self.user_file = workspace / "USER.md"
        self._cursor_file = self.memory_dir / ".cursor"
        self._dream_cursor_file = self.memory_dir / ".dream_cursor"
        self._review_state_file = self.memory_dir / ".dream_review"
        self._corruption_logged = False  # rate-limit non-int cursor warning
        self._malformed_entry_logged = False  # rate-limit bad history shape warning
        self._oversize_logged = False  # rate-limit oversized-entry warning
        self._append_lock = threading.Lock()  # serialize cursor allocation + append

    # -- generic helpers -----------------------------------------------------

    @staticmethod
    def read_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    # -- MEMORY.md (long-term facts) -----------------------------------------

    def read_memory(self) -> str:
        return self.read_file(self.memory_file)

    # -- WIKI.md (wiki directory, managed by Atlas) --------------------------

    def read_wiki_memory(self) -> str:
        return self.read_file(self.wiki_file)

    # -- context injection (used by context.py) ------------------------------

    def get_memory_context(self) -> str:
        long_term = self.read_memory()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    def get_wiki_memory_context(self, max_tokens: int | None = None) -> str:
        """Blocco rubrica per il system prompt, troncato al tetto configurato.

        Il troncamento sta qui e non nel prompt di Atlas perché è l'ultima
        linea di difesa: un run che produce un file lungo il doppio del dovuto
        peserebbe altrimenti su ogni turno fino al run successivo.
        """
        content = self.read_wiki_memory().strip()
        if not content:
            return ""
        if max_tokens is not None and max_tokens > 0:
            content = truncate_text_to_tokens(content, max_tokens)
        return f"## Wiki Directory\n{content}"

    def get_archive_context(self) -> str:
        """Una riga sola per dire che il tier freddo esiste, o stringa vuota.

        Serve per la stessa ragione per cui Atlas funziona: **un indice che
        nessuno sa esistere non viene mai aperto**. Un archivio invisibile al
        modello è indistinguibile, dal suo punto di vista, da una cancellazione —
        e allora tanto varrebbe cancellare.

        Tre vincoli, e sono ciò che tiene la riga onesta:

        - **È piatta nella dimensione dell'archivio.** Un abstract per voce
          spenderebbe il budget caldo che questa fase esiste per proteggere. Qui
          c'è un numero, e cresce di zero token quando l'archivio raddoppia.
        - **Sparisce quando l'archivio è vuoto**, così un'installazione nuova non
          paga niente per una cartella che non esiste ancora.
        - **Dice esplicitamente che lì non si scrive.** Il percorso, nominato in un
          prompt che riceve anche Dream, sarebbe altrimenti un invito: la sua
          allowlist non lo comprende, e per lui una scrittura rifiutata non è un
          tentativo a vuoto ma un run intero che non commette niente. La
          degradazione la fa il runtime (v. ``tools/memory_entries.py``), e questa
          riga lo dichiara invece di lasciarlo indovinare.
        """
        directory = archive_dir(self.memory_dir)
        try:
            count = sum(1 for _ in directory.glob("*.md"))
        except OSError:
            return ""
        if not count:
            return ""
        return (
            "## Archive\n"
            f"Facts removed from long-term memory are moved to `memory/archive/` "
            f"({count} so far), never deleted — one file each, the fact itself as the body. "
            "The runtime files them there; you never write to that directory. "
            "Use the `recall` tool to read it — not `grep`, which matches on "
            "substrings and so cannot find an Italian fact from an English "
            "question, and skips large files without saying so."
        )

    def get_known_facts_context(
        self,
        *,
        max_tokens: int = _KNOWN_FACTS_MAX_TOKENS,
        pending_entries: int = _KNOWN_FACTS_PENDING_ENTRIES,
    ) -> str:
        """Ciò che la memoria già registra, per il prompt del Consolidator.

        Il difetto che questo blocco chiude (``D5``) è che il Consolidator
        estrae alla cieca: non vede cosa è già stato consolidato, quindi
        riestrae gli stessi fatti a ogni passaggio. Il costo non è teorico — è
        un turno LLM, rumore in ``history.jsonl``, e un batch di soli duplicati
        che a valle si legge come "Dream non ha consolidato niente", che è
        falso e che è la ragione per cui a un certo punto è servita una soglia
        di pressione per indovinarlo.

        **Due sorgenti, e la seconda è quella che conta.** I file caldi dicono
        cosa Dream ha già archiviato; la coda di history oltre il cursore di
        Dream dice cosa è già stato estratto e sta aspettando. Con i soli file,
        una conversazione lunga — consolidata tre volte prima che Dream giri
        una — resterebbe duplicata esattamente come prima, perché al momento
        della seconda estrazione nei file non c'è ancora niente.

        **Quindi la coda si serve per prima, con una quota sua.** Misurato sul
        Titan 2 il 2026-08-19: il blocco stava a 5.239 caratteri contro un
        tetto di 4.800, e il troncamento tagliava proprio le voci in attesa,
        che stavano in fondo — la sorgente dominante nella posizione che si
        perde per prima. Le due quote non sono simmetriche e non devono
        esserlo: se il tetto lo prendesse tutto la coda, un Dream fermo da
        giorni — cioè esattamente il guasto per cui esiste questo piano —
        cancellerebbe i file dal blocco e farebbe riestrarre tutto USER.md.

        **Solo voci intere.** Un fatto tagliato a metà in un elenco che dice
        "questi sono già registrati" è peggio di un fatto assente: si legge
        come un fatto *diverso*, e il confronto che il blocco esiste per
        permettere diventa un confronto con qualcosa che nessuno ha mai
        scritto. Ciò che non entra viene contato in chiaro, così il modello sa
        di leggere un elenco parziale invece di dedurlo.

        **Le istruzioni stanno prima dell'elenco**, e non è impaginazione: ciò
        che si perde per primo dev'essere un fatto in meno da confrontare, mai
        la regola su come confrontarli.

        Stringa vuota quando non c'è niente da mostrare, per la stessa ragione
        di :meth:`get_archive_context`: un'installazione nuova non paga token
        per dichiarare che la memoria è vuota.
        """
        from jenny.agent.tools.memory_entries import entry_id, parse_entries

        seen: set[str] = set()

        pending_facts: list[str] = []
        pending = self.read_unprocessed_history(since_cursor=self.get_last_dream_cursor())
        for record in pending[-pending_entries:] if pending_entries > 0 else []:
            for mark, fact in iter_fact_lines(record.get("content", "")):
                # ``[skip]`` non è un fatto registrato: è un fatto che il
                # Consolidator ha già giudicato non degno. Mostrarlo qui
                # direbbe "questo è in memoria", che è il contrario del vero.
                if mark == "skip":
                    continue
                bullet = f"- {fact}"
                fid = entry_id(bullet)
                if fid not in seen:
                    seen.add(fid)
                    pending_facts.append(bullet)

        file_facts: list[str] = []
        for text in (self.read_file(self.user_file), self.read_memory()):
            for entry in parse_entries(text):
                if entry.id not in seen:
                    seen.add(entry.id)
                    file_facts.append(entry.text.strip())

        if not pending_facts and not file_facts:
            return ""

        head = "\n".join([
            "## Already recorded",
            "",
            "These facts are already in long-term memory, or are already extracted and "
            "waiting to be filed. Do not extract them again: a chunk of duplicates costs "
            "a full consolidation turn and is discarded downstream.",
            "",
            "Match on substance, not on wording. Two things are still worth extracting, "
            "and they are why this is a list rather than a blanket \"skip what you have "
            "seen before\":",
            "",
            # Numerate, non puntate: sotto, ogni riga che comincia con un
            # trattino è un fatto registrato, e due istruzioni travestite da
            # voci dell'elenco sarebbero due fatti che la memoria non contiene.
            "1. A fact that **changes or contradicts** one of these. Mark it [correction] "
            "and say what changed — a memory that cannot be updated is worse than one "
            "that repeats itself.",
            "2. A fact that **adds** to one of these: a detail, a limit, a case where it "
            "does not hold. That is new information about a known subject, not a repeat.",
            "",
        ])

        budget = (max_tokens * 4 - len(head)) if max_tokens > 0 else None
        if budget is not None and budget <= 0:
            return head.rstrip() + "\n"

        pending_budget = None if budget is None else int(budget * _KNOWN_FACTS_PENDING_SHARE)
        kept_pending, dropped = _pack_entries(pending_facts, pending_budget)
        # Ciò che la coda non ha speso torna ai file: la quota è un pavimento
        # per la coda, non un soffitto per il resto.
        file_budget = None if budget is None else budget - _entries_cost(kept_pending)
        kept_files, dropped_files = _pack_entries(file_facts, file_budget)
        dropped += dropped_files

        lines = [head, *kept_pending, *kept_files]
        if dropped:
            # Riga piatta e senza trattino: sopra, un trattino significa "fatto
            # registrato", e questa non lo è.
            lines.append(f"\n({dropped} further recorded facts are not listed here.)")
        return "\n".join(lines)

    # -- history.jsonl — append-only, JSONL format ---------------------------

    def append_history(
        self,
        entry: str,
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> int:
        """Append *entry* to history.jsonl and return its auto-incrementing cursor.

        Entries are passed through `strip_think` to drop template-level leaks
        (e.g. unclosed `<think` prefixes, `<channel|>` markers) before being
        persisted. If the cleaned content is empty but the raw entry wasn't,
        the record is persisted with an empty string rather than falling back
        to the raw leak — otherwise `strip_think`'s guarantees would be
        undone by history replay / consolidation downstream.

        A defensive cap (*max_chars*, default ``_HISTORY_ENTRY_HARD_CAP``) is
        applied as a final safety net: individual callers should cap their own
        content more tightly; this default only exists to catch unintentional
        large writes (e.g. an LLM echoing its input back as a "summary").
        """
        limit = max_chars if max_chars is not None else _HISTORY_ENTRY_HARD_CAP
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = entry.rstrip()
        if len(raw) > limit:
            if not self._oversize_logged:
                self._oversize_logged = True
                logger.warning(
                    "history entry exceeds {} chars ({}); truncating. "
                    "Usually means a caller forgot its own cap; "
                    "further occurrences suppressed.",
                    limit, len(raw),
                )
            raw = truncate_text(raw, limit)
        content = strip_think(raw)
        # Cursor allocation and the append must be atomic: concurrent writers
        # could otherwise read the same current cursor and emit duplicates.
        with self._append_lock:
            cursor = self._next_cursor()
            if raw and not content:
                logger.debug(
                    "history entry {} stripped to empty (likely template leak); "
                    "persisting empty content to avoid re-polluting context",
                    cursor,
                )
            record = {"cursor": cursor, "timestamp": ts, "content": content}
            if session_key:
                record["session_key"] = session_key
            with open(self.history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            # Small full-file replacement each append: use the same atomic
            # temp-file+fsync+rename helper as every other on-disk cursor/state
            # file in this codebase (cron store, session manager, sidebar
            # state, ...), rather than a bare write_text with no durability.
            atomic_write(self._cursor_file, str(cursor))
        return cursor

    @staticmethod
    def _valid_cursor(value: Any) -> int | None:
        """Int cursors only — reject bool (``isinstance(True, int)`` is True)."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _iter_valid_entries(self) -> Iterator[tuple[dict[str, Any], int]]:
        """Yield ``(entry, cursor)`` for well-formed entries; warn once on corruption."""
        poisoned: Any = None
        malformed_cursor: int | None = None
        for entry in self._read_entries():
            raw = entry.get("cursor")
            if raw is None:
                continue
            cursor = self._valid_cursor(raw)
            if cursor is None:
                poisoned = raw
                continue
            if not self._valid_history_payload(entry):
                malformed_cursor = cursor
                continue
            yield entry, cursor
        if poisoned is not None and not self._corruption_logged:
            self._corruption_logged = True
            logger.warning(
                "history.jsonl contains a non-int cursor ({!r}); dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                poisoned,
            )
        if malformed_cursor is not None and not self._malformed_entry_logged:
            self._malformed_entry_logged = True
            logger.warning(
                "history.jsonl contains a malformed entry at cursor {}; dropping it. "
                "Usually caused by an external writer; further occurrences suppressed.",
                malformed_cursor,
            )

    @staticmethod
    def _valid_history_payload(entry: dict[str, Any]) -> bool:
        if not isinstance(entry.get("timestamp"), str):
            return False
        if not isinstance(entry.get("content"), str):
            return False
        session_key = entry.get("session_key")
        return session_key is None or isinstance(session_key, str)

    def _next_cursor(self) -> int:
        """Return the next cursor value, robust to a crash between the history
        append and the ``.cursor`` write.

        ``append_history`` fsyncs the new entry *before* atomically rewriting
        ``.cursor``; a process kill in that window (frequent on Android) leaves
        ``.cursor`` one behind the last persisted entry.  Trusting ``.cursor``
        alone would then re-allocate a cursor already on disk, producing
        duplicates that break ``read_unprocessed_history`` and the Dream
        cursor.  So we always consider *both* sources — the ``.cursor`` file
        and the last persisted entry — and take the maximum.  ``max`` also
        preserves monotonicity in the inverse case (history externally
        truncated below a higher ``.cursor``): a cursor is never reused.
        """
        candidates: list[int] = []
        if self._cursor_file.exists():
            with suppress(ValueError, OSError):
                file_cursor = self._valid_cursor(
                    int(self._cursor_file.read_text(encoding="utf-8").strip())
                )
                if file_cursor is not None:
                    candidates.append(file_cursor)
        last = self._read_last_entry() or {}
        entry_cursor = self._valid_cursor(last.get("cursor"))
        if entry_cursor is not None:
            candidates.append(entry_cursor)
        if candidates:
            return max(candidates) + 1
        # Both fast paths unusable — scan the whole file and take ``max``,
        # which stays correct even if the monotonic invariant was broken by
        # external writes.
        return max((c for _, c in self._iter_valid_entries()), default=0) + 1

    def read_unprocessed_history(self, since_cursor: int) -> list[dict[str, Any]]:
        """Return history entries with a valid cursor > *since_cursor*."""
        return [e for e, c in self._iter_valid_entries() if c > since_cursor]

    @classmethod
    def _is_internal_history_session(cls, session_key: str | None) -> bool:
        """True se la voce di history viene da lavoro interno, non dall'utente.

        Il vocabolario e' quello unico di :mod:`jenny.session.keys`: qui resta
        solo la guardia su ``None``/stringa vuota, che il predicato canonico non
        ha perche' lavora su chiavi di sessione sempre presenti, mentre il
        ``session_key`` di una voce di history e' opzionale.
        """
        if not session_key:
            return False
        return is_internal_session_key(session_key)

    def read_recent_history_for_prompt(
        self,
        since_cursor: int,
        *,
        session_key: str | None,
    ) -> list[dict[str, Any]]:
        """Return unprocessed history entries safe to inject into a turn prompt."""
        entries = self.read_unprocessed_history(since_cursor=since_cursor)
        if session_key is None:
            return entries
        return [
            entry
            for entry in entries
            if (entry_session := entry.get("session_key")) == session_key
            or not self._is_internal_history_session(entry_session)
        ]

    def compact_history(self) -> None:
        """Drop oldest entries if the file exceeds *max_history_entries*.

        The read→rewrite must hold ``_append_lock``: ``append_history`` runs on
        real threads (Consolidator via ``asyncio.to_thread``) and fsyncs a new
        entry before returning. Without the lock, an append landing between our
        ``_read_entries`` and the atomic ``_write_entries`` rewrite would be
        silently dropped by the rename. Holding the lock serializes the two:
        a concurrent append blocks until the rewrite completes, then appends on
        top of the compacted file.

        No self-deadlock: ``_append_lock`` is a non-reentrant ``threading.Lock``
        and nothing under it re-enters ``append_history`` or ``compact_history``
        (``_read_entries`` / ``_write_entries`` are pure file I/O). No caller
        holds the lock when invoking this method.
        """
        if self.max_history_entries <= 0:
            return
        with self._append_lock:
            entries = self._read_entries()
            if len(entries) <= self.max_history_entries:
                return
            kept = entries[-self.max_history_entries:]
            self._write_entries(kept)

    # -- JSONL helpers -------------------------------------------------------

    def _read_entries(self) -> list[dict[str, Any]]:
        """Read all entries from history.jsonl."""
        entries: list[dict[str, Any]] = []
        with suppress(FileNotFoundError):
            with open(self.history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        return entries

    def _read_last_entry(self) -> dict[str, Any] | None:
        """Read the last entry from the JSONL file efficiently."""
        try:
            with open(self.history_file, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return None
                read_size = min(size, 4096)
                f.seek(size - read_size)
                data = f.read().decode("utf-8")
                lines = [line for line in data.split("\n") if line.strip()]
                if not lines:
                    return None
                return json.loads(lines[-1])
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        """Overwrite history.jsonl with the given entries (atomic write)."""
        content = "".join(
            json.dumps(entry, ensure_ascii=False) + "\n"
            for entry in entries
        )
        atomic_write(self.history_file, content)

    # -- dream cursor --------------------------------------------------------

    def get_last_dream_cursor(self) -> int:
        if self._dream_cursor_file.exists():
            with suppress(ValueError, OSError):
                return int(self._dream_cursor_file.read_text(encoding="utf-8").strip())
        return 0

    def set_last_dream_cursor(self, cursor: int) -> None:
        # Stesso helper del cursore di history (vedi ``append``): un
        # write_text nudo qui lascerebbe, se il processo muore a metà, un file
        # vuoto o parziale — cioè un cursore che ``get_last_dream_cursor``
        # legge come 0 e Dream ricomincia da capo su tutta la storia.
        atomic_write(self._dream_cursor_file, str(cursor))

    # -- review pass state ---------------------------------------------------

    @classmethod
    def _review_counter(cls, value: Any) -> int:
        """Normalizza un contatore del review state: qualunque cosa strana → 0.

        Il rifiuto di ``bool`` viene da ``_valid_cursor`` (``isinstance(True,
        int)`` è True, e un ``true`` finito nel JSON conterebbe come 1). I
        negativi vanno a 0 e non passano così come sono: un contatore negativo
        letto dal disco rimanderebbe il review pass indietro nel tempo invece
        che avanti, cioè lo spegnerebbe in silenzio per N cicli.
        """
        counter = cls._valid_cursor(value)
        if counter is None or counter < 0:
            return 0
        return counter

    def get_review_state(self) -> tuple[int, int]:
        """Return ``(runs_since_review, stuck_runs)`` for the Dream review pass.

        Lettura tollerante quanto ``get_last_dream_cursor``: file assente, JSON
        troncato, radice non-dict, chiavi mancanti o di tipo sbagliato danno
        tutti ``(0, 0)`` e mai un'eccezione. Su Android il processo può essere
        ucciso in qualsiasi momento e questo file resta a metà: se la lettura
        sollevasse, il run di Dream che la chiama morirebbe prima di consolidare
        nulla — molto peggio del ripartire da zero, che al più ritarda un review
        pass di N cicli.

        ``runs_since_review`` conta i cicli dall'ultimo review pass;
        ``stuck_runs`` i run consecutivi in cui Dream ha tentato scritture senza
        riuscirne nessuna (v. ``dream_should_advance_cursor``: quei run non
        avanzano il cursore, e se si ripetono serve forzare un review invece di
        rimacinare per sempre lo stesso batch). Entrambi i contatori li consuma
        il chiamante: qui c'è solo lo stato su disco.
        """
        try:
            raw = self._review_state_file.read_text(encoding="utf-8")
        except OSError:
            return (0, 0)
        try:
            data = json.loads(raw)
        except ValueError:  # include JSONDecodeError
            return (0, 0)
        if not isinstance(data, dict):
            return (0, 0)
        return (
            self._review_counter(data.get("runs_since_review")),
            self._review_counter(data.get("stuck_runs")),
        )

    def get_nothing_new_runs(self) -> int:
        """Run consecutivi in cui Dream non ha consolidato **senza** rifiuti.

        Quarto campo di ``.dream_review``, letto a parte come
        :meth:`get_review_forced_at_stuck` e per la stessa ragione: non cambiare
        la firma di :meth:`get_review_state` e i suoi chiamanti.

        Perché è separato da ``stuck_runs``. Quel contatore conta va bene per
        "Dream non consolida", ma la domanda che governa il rimedio è un'altra:
        **un review pass può servire a qualcosa?** Se una scrittura è stata
        rifiutata dal tetto, sì — c'è spazio da liberare e forzarlo ha senso. Se
        invece non è stato rifiutato niente e comunque non è atterrato nulla, un
        review non ha nessuna leva: potherebbe file che non hanno niente da dare.
        Misurato sul Titan 2 il 2026-08-18, un review forzato su file al 77% e
        79% che non avevano niente da liberare.

        Uno stato scritto prima che questo campo esistesse legge ``0``, ed è la
        lettura giusta: il vecchio ``stuck_runs`` si eredita come *no room*, che è
        il ramo che tiene armata la via d'uscita dal livelock.
        """
        try:
            data = json.loads(self._review_state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        return self._review_counter(data.get("nothing_new_runs"))

    def get_review_forced_at_stuck(self) -> int:
        """A quale valore di ``stuck_runs`` il review è stato forzato l'ultima volta.

        Terzo campo dello stesso file, letto a parte per non cambiare la firma di
        :meth:`get_review_state` e i suoi chiamanti. ``0`` significa "mai forzato",
        che è anche il valore che si legge da uno stato scritto prima che questo
        campo esistesse: la prima volta il review si riforza, e da lì in poi il
        conto è giusto.

        Perché serve. Il review forzato dal livelock scatta su
        ``stuck % STUCK_FORCES_REVIEW == 0``, e ``stuck`` **non** viene toccato
        quando non c'era storia da consolidare (``advanced is None``). Su
        un'installazione in pari, con ``stuck`` fermo su un multiplo della soglia,
        quella condizione è vera a *ogni* run: un turno LLM di review ogni due ore
        per sempre — lo specchio esatto del livelock che il contatore esiste per
        chiudere. Ricordando a che valore si è già forzato, non si riforza finché
        quel valore non cambia, cioè finché Dream non manca un altro consolidamento.

        Il campo vale però solo dentro la salita di ``stuck`` che lo ha prodotto, e
        :meth:`set_review_state` lo azzera insieme al contatore: sopravvivergli lo
        trasformerebbe da freno in blocco — v. il commento lì.
        """
        try:
            data = json.loads(self._review_state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        return self._review_counter(data.get("forced_at_stuck"))

    def set_review_state(
        self,
        *,
        runs_since_review: int,
        stuck_runs: int,
        forced_at_stuck: int | None = None,
        nothing_new_runs: int | None = None,
    ) -> None:
        """Persist the review-pass counters to ``.dream_review``.

        *forced_at_stuck* a ``None`` — il default — **conserva** il valore su
        disco: i chiamanti che aggiornano solo i due contatori non devono
        conoscerlo, e non devono poterlo azzerare per omissione. Lo passa solo chi
        forza il review (v. :meth:`get_review_forced_at_stuck`). Con un'eccezione, che
        è un invariante del file e non una scelta del chiamante: scrivere
        ``stuck_runs=0`` azzera anche ``forced_at_stuck``.
        """
        # Stesso helper del cursore di Dream (v. ``set_last_dream_cursor``): un
        # write_text nudo lascerebbe, se il processo muore a metà, un JSON
        # troncato — che ``get_review_state`` legge come (0, 0), cioè un review
        # pass appena fatto anche se non è mai partito. Con ``atomic_write`` il
        # file o è quello vecchio o è quello nuovo, mai una via di mezzo.
        # I valori si normalizzano anche in scrittura: un contatore negativo
        # arrivato da un chiamante non deve nemmeno toccare il disco.
        forced = (
            self.get_review_forced_at_stuck() if forced_at_stuck is None else forced_at_stuck
        )
        stuck = self._review_counter(stuck_runs)
        # Stessa convenzione di ``forced_at_stuck``: ``None`` conserva. I due
        # contatori si azzerano però **insieme**, perché a azzerarli è lo stesso
        # evento — il cursore che avanza — e uno dei due rimasto su per omissione
        # racconterebbe un blocco che non c'è.
        nothing_new = (
            self.get_nothing_new_runs() if nothing_new_runs is None else nothing_new_runs
        )
        nothing_new = self._review_counter(nothing_new)
        # ``forced_at_stuck`` indicizza la salita di ``stuck`` che lo ha prodotto:
        # dice "a questo valore il review l'ho già forzato, non riforzarlo". Azzerato
        # ``stuck`` — cioè quando il cursore è avanzato e quella salita è finita — il
        # valore resta a galleggiare, e alla salita successiva ``dream_cycle`` ritrova
        # ``stuck == forced_at`` proprio al run in cui il review servirebbe: la
        # condizione ``stuck != forced_at`` è falsa e il freno diventa un blocco.
        # Misurato sul Titan 2 il 2026-08-18: ``forced_at_stuck: 2`` avanzato da un
        # episodio già chiuso, ``stuck`` di nuovo a 2, nessun review — è arrivato solo
        # a 4, sulla soglia d'allarme, due run oltre il suo scopo.
        #
        # Sta qui e non nel chiamante perché è una proprietà del file, non una
        # politica: i due campi non possono raccontare stati diversi. E non scarta la
        # scelta di nessuno — l'unico chiamante che passa un ``forced_at_stuck``
        # esplicito è il ramo livelock, che richiede ``stuck > 0``.
        if stuck == 0:
            forced = 0
        payload = json.dumps({
            "runs_since_review": self._review_counter(runs_since_review),
            "stuck_runs": stuck,
            "forced_at_stuck": self._review_counter(forced),
            "nothing_new_runs": nothing_new,
        })
        atomic_write(self._review_state_file, payload)

    def build_dream_prompt(
        self, *, max_entries: int = 20, gauge: str = "",
    ) -> tuple[str, int] | None:
        """Build the Dream prompt with unprocessed history context.

        Returns ``(prompt, last_cursor)`` or ``None`` if nothing to process.

        *gauge* è la riga di riempimento dei file di memoria (es.
        ``MEMORY.md [67% — 1.474/2.200 char]``) da rendere nel prompt come
        ``budget_gauge``; con la stringa vuota — il default — la sezione Budget
        del template sparisce e il prompt resta identico a prima. Il calcolo
        sta nel chiamante di proposito: ``MemoryStore`` è uno strato di I/O
        puro e dargli qui la config per misurare il budget lo legherebbe al
        modulo che quel budget lo impone.
        """
        last_cursor = self.get_last_dream_cursor()
        entries = self.read_unprocessed_history(since_cursor=last_cursor)
        if not entries:
            return None

        batch = entries[:max_entries]
        history_text = "\n".join(
            f"[{e['timestamp']}] {truncate_text(e['content'], 500)}"
            for e in batch
        )
        skill_creator_path = str(self.workspace / "skills" / "skill-creator" / "SKILL.md")
        template = render_template(
            "agent/dream.md",
            strip=True,
            skill_creator_path=skill_creator_path,
            budget_gauge=gauge,
        )
        prompt = f"{template}{DREAM_HISTORY_HEADER}{history_text}"
        return (prompt, batch[-1]["cursor"])

    @staticmethod
    def dream_prompt_history(prompt: str) -> str:
        """La sola parte di *storia* di un prompt di Dream, senza il template.

        Serve a chi deve decidere qualcosa **sul batch** — oggi
        ``dream_cycle.batch_carries_retained_facts`` — e vive qui perché qui il
        prompt viene incollato: dove finisce il template e comincia la storia è
        una cosa che sa un modulo solo.

        E non è una comodità. Il template di Dream *nomina* i tag di ritenzione
        (``[durable]``, ``[permanent]``, ``[correction]``) nella sua sezione
        "History attribute tags": cercarli nel prompt intero li trova **sempre**,
        su qualunque batch, anche su uno che non ne contiene nessuno. Un predicato
        costruito così è vero per costruzione, cioè non è un predicato. Da qui il
        ritaglio, e il test che lo prova su un batch senza tag.

        Su un prompt che l'header non contiene ritorna stringa vuota: chi non ha
        storia non ha batch, ed è la risposta conservativa giusta.
        """
        _, _, history = prompt.partition(DREAM_HISTORY_HEADER)
        return history

    def build_dream_tools(
        self, *, write_size_guard: Callable[[Path, str], str | None] | None = None,
    ):
        """Build the restricted tool registry used by Dream runs.

        Il ``FileStates`` creato per il run viene esposto come attributo
        ``file_states`` del registry restituito: è per-run (nessuna condivisione
        tra Dream concorrenti) e traccia scritture tentate/riuscite, così il
        chiamante può decidere via :meth:`dream_should_advance_cursor` se
        avanzare il cursore.

        *write_size_guard* è il gancio pre-scrittura che impone il budget dei
        file di memoria: riceve il path risolto e il testo che finirebbe su
        disco, ritorna ``None`` per lasciar passare o il messaggio di rifiuto
        da restituire al modello. Il tipo è dichiarato per struttura e non
        importato da chi lo costruisce: questo modulo è I/O puro e non deve
        dipendere dal modulo che misura il budget.
        """
        from jenny.agent.tools.apply_patch import ApplyPatchTool
        from jenny.agent.tools.file_state import FileStates
        from jenny.agent.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
        from jenny.agent.tools.memory_entries import MemoryEntryTool, make_entry_archiver
        from jenny.agent.tools.registry import ToolRegistry

        tools = ToolRegistry()
        file_states = FileStates()
        # Canonicalizza la radice del workspace e i file editabili. Su Android il
        # filesystem espone la dir dati come ``/data/user/0/<pkg>`` ma ``.resolve()``
        # la canonicalizza in ``/data/data/<pkg>``: se la base di risoluzione dei
        # path e la allowlist di file esatti (``extra_write_allowed_files``) restano
        # in forme diverse, il guard anti-symlink di ``_is_path_exactly_allowed``
        # (logico via ``abspath`` vs risolto via ``.resolve()``) scatta e Dream non
        # riesce a scrivere MEMORY/SOUL/USER. Risolvendo entrambi i lati qui le due
        # forme coincidono, senza indebolire la protezione contro gli escape via
        # symlink *interni* al workspace (lì logico e risolto continuano a divergere).
        workspace = self.workspace.resolve()
        skills_dir = workspace / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        extra_read = [skills_dir] if skills_dir.exists() else None
        # Degradazione al **confine del file**, non dentro un tool. Il review pass
        # pota con ``apply_patch``/``edit_file`` sui file interi — gli serve per
        # ristrutturare — quindi una difesa che vivesse solo in ``memory remove``
        # lascerebbe scoperto proprio lo scrittore i cui errori sono definitivi
        # (v. 2.4b del piano). Passandolo a tutti e quattro, qualunque cosa
        # riscriva USER.md o memory/MEMORY.md fa passare dall'archivio le voci che
        # sta per togliere, senza che nessuno debba ricordarsene.
        entry_archiver = make_entry_archiver(workspace)
        editable_files = [
            self.memory_file.resolve(),
            self.soul_file.resolve(),
            self.user_file.resolve(),
        ]

        # Il guard va a tutti e quattro, ``ReadFileTool`` compreso, dove oggi
        # non fa nulla: è il costruttore che decide cosa passa, non un elenco
        # di tool "scrivibili" tenuto a mano. Un tool che domani diventasse
        # write-capable — o un ``read_file`` con un ``--write-back`` — sfuggirebbe
        # al budget solo perché nessuno si è ricordato di aggiungerlo qui.
        tools.register(ReadFileTool(
            workspace=workspace,
            allowed_dir=workspace,
            extra_read_allowed_dirs=extra_read,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        tools.register(EditFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        tools.register(ApplyPatchTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            extra_write_allowed_files=editable_files,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        tools.register(WriteFileTool(
            workspace=workspace,
            allowed_dir=skills_dir,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        ))
        # Scrittura per voce su USER.md e memory/MEMORY.md, accanto — non al posto
        # — dei tool sopra: il review pass ristruttura davvero quei file, e per
        # farlo gli serve riscriverli interi. Quello che cambia è il turno
        # incrementale, dove aggiungere un fatto smette di essere una riscrittura
        # e diventa un'aggiunta, con un id da citare.
        #
        # Montato qui e non in ``_HARDCODED_TOOL_MODULES``: quella lista è il
        # registry dell'agente principale, e la decisione del 2026-08-18 è di
        # dare il tool prima a Dream soltanto. Se ha un difetto, scoprirlo in un
        # run notturno costa un batch rigiocato; scoprirlo in chat costa il turno
        # dell'utente.
        memory_entries = MemoryEntryTool(
            workspace,
            file_states=file_states,
            write_size_guard=write_size_guard,
            entry_archiver=entry_archiver,
        )
        tools.register(memory_entries)
        # Esposto per ``dream_should_advance_cursor``: stesso oggetto usato da
        # tutti i tool sopra (passato esplicitamente ai costruttori), quindi
        # riflette le scritture del run.
        tools.file_states = file_states
        # Esposto come ``file_states`` e per la stessa ragione: il chiamante deve
        # poter chiedere com'è andato il run senza rifare le misure. Qui però la
        # risposta è in voci — quante ne sono entrate, quante sostituite, quante
        # erano già lì — che è ciò che rende ``batch_was_not_consolidated`` una
        # verifica invece di una stima.
        tools.memory_entries = memory_entries
        return tools

    @staticmethod
    def internal_run_completed(resp: object | None) -> bool:
        """Return True only when an ephemeral internal agent turn completed cleanly."""
        metadata = getattr(resp, "metadata", None)
        return isinstance(metadata, dict) and metadata.get("_stop_reason") == "completed"

    @staticmethod
    def dream_run_completed(resp: object | None) -> bool:
        """Return True only when an ephemeral Dream agent turn completed cleanly."""
        return MemoryStore.internal_run_completed(resp)

    @staticmethod
    def internal_run_should_commit(
        resp: object | None,
        file_states: object | None,
    ) -> bool:
        """Return True quando un run interno può registrare il proprio progresso.

        Regola condivisa da Dream (avanzamento del cursore su ``history.jsonl``)
        e da Atlas (avanzamento del fingerprint della wiki). In entrambi i casi
        il progresso è un'affermazione — "questo input è stato digerito" — e
        farla dopo un run che non ha prodotto nulla per un blocco di policy
        significa perdere quell'input per sempre. Si registra quindi solo se il
        run:

        - è completato pulito (``internal_run_completed``), **e**
        - nessun rifiuto di budget è rimasto aperto
          (``unrecovered_refusals == 0``: un file rifiutato e poi riscritto
          accorciato non conta più), **e**
        - ha scritto almeno un file (``writes_ok > 0``), **oppure** non ha mai
          tentato una scrittura (``writes_attempted == 0``) — il caso legittimo
          "non c'era niente da cambiare".

        Se ha tentato scritture e nessuna è riuscita NON si registra: l'input va
        riprocessato al run seguente.

        Il rifiuto di budget va guardato a parte dai due contatori aggregati: un
        run che scrive con successo una skill e si vede rifiutare ``MEMORY.md`` ha
        comunque ``writes_ok > 0``, e su ``ok``/``attempted`` passerebbe per
        riuscito. Il fatto rifiutato non è su disco e, registrato il progresso, non
        tornerebbe in nessun batch successivo: perso.

        Ma il rifiuto che conta è quello **rimasto aperto**, non quello avvenuto —
        ed è una misura di *contenuto*, non un conteggio per run. Il messaggio di
        rifiuto chiede al modello di liberare spazio e riscrivere nello stesso
        turno; se obbedisce e il contenuto atterra, il run ha fatto il suo lavoro.
        Guardare il contatore cumulativo trattava quel successo come un
        fallimento: cursore fermo, stesso batch due ore dopo, ``stuck`` in salita e
        un allarme che annunciava scritture rifiutate che erano riuscite — con i
        tetti armati, lo stato normale e non un caso limite. Si legge quindi
        ``unrecovered_refusals``, che si chiude solo quando una scrittura riuscita
        su *quel* percorso fa atterrare almeno una delle righe rifiutate
        (v. ``FileStates.record_write_refused`` per il perché di "almeno una").

        Il livelock che resta — rifiuto che nessuno recupera — esce dal review
        forzato (v. ``agent/dream_cycle.py``), non da un commit più permissivo.

        ``file_states`` è tollerante a ``None`` / oggetti senza i contatori di
        scrittura (fallback conservativo: nessun avanzamento) per non far
        esplodere il chiamante se il registry non è quello costruito qui. Un
        registry senza ``unrecovered_refusals`` ripiega sul contatore cumulativo
        — comportamento di prima, che per chi non ha il gancio è identico — e in
        assenza di entrambi vale zero: chi non ha i contatori non ha nemmeno il
        gancio che li incrementa (``_FsTool._check_write_size``), quindi non può
        aver rifiutato nulla.
        """
        if not MemoryStore.internal_run_completed(resp):
            return False
        writes_ok = getattr(file_states, "writes_ok", None)
        writes_attempted = getattr(file_states, "writes_attempted", None)
        if not isinstance(writes_ok, int) or not isinstance(writes_attempted, int):
            return False
        outstanding = getattr(file_states, "unrecovered_refusals", None)
        if not isinstance(outstanding, int):
            outstanding = getattr(file_states, "writes_refused_budget", 0)
        if isinstance(outstanding, int) and outstanding > 0:
            return False
        if writes_ok > 0:
            return True
        return writes_attempted == 0

    @staticmethod
    def dream_should_advance_cursor(
        resp: object | None,
        file_states: object | None,
    ) -> bool:
        """Return True only when the Dream cursor may safely advance.

        Un turno che completa pulito non basta: se Dream non produce alcuna
        scrittura perché è stato bloccato (policy) o ha rifiutato, avanzare il
        cursore perderebbe per sempre quelle voci di history (consolidamento
        silenziosamente saltato). Perciò si avanza solo quando il run:

        - è completato pulito (``dream_run_completed``), **e**
        - non ha rifiuti di budget rimasti aperti — un file rifiutato e poi
          riscritto accorciato nello stesso turno è recuperato, non perso, **e**
        - ha scritto almeno un file (``writes_ok > 0``), **oppure** non ha mai
          tentato una scrittura (``writes_attempted == 0``) — il caso legittimo
          "nulla da consolidare".

        Se invece ha tentato scritture ma nessuna è riuscita (tutte bloccate o
        fallite) NON si avanza: quelle voci vanno riprocessate al run seguente.
        Lo stesso vale se *una parte* delle scritture è passata e una è stata
        rifiutata dal budget — v. :meth:`internal_run_should_commit`.

        ``file_states`` è tollerante a ``None`` / oggetti senza i contatori
        (fallback conservativo: nessun avanzamento) per non far esplodere il
        chiamante se il registry non è quello di :meth:`build_dream_tools`.
        """
        return MemoryStore.internal_run_should_commit(resp, file_states)

    # -- message formatting utility ------------------------------------------

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    def raw_archive(
        self,
        messages: list[dict],
        *,
        max_chars: int | None = None,
        session_key: str | None = None,
    ) -> None:
        """Fallback: dump raw messages to history.jsonl without LLM summarization."""
        limit = max_chars if max_chars is not None else _RAW_ARCHIVE_MAX_CHARS
        formatted = truncate_text(self._format_messages(messages), limit)
        self.append_history(
            f"[RAW] {len(messages)} messages\n"
            f"{formatted}",
            session_key=session_key,
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )

    # ------------------------------------------------------------------
    # Dream helpers
    # ------------------------------------------------------------------

    @staticmethod
    def dream_session_key() -> str:
        """Return a unique session key for a Dream run, e.g. ``dream:20260528-100000``."""
        return f"{DREAM_SESSION_PREFIX}{datetime.now():%Y%m%d-%H%M%S}"

    @staticmethod
    def prune_internal_sessions(
        sessions_dir: Path, prefix: str, *, keep: int = 10
    ) -> list[str]:
        """Remove the oldest ``<prefix>_*.jsonl`` session files, keeping N.

        Only files matching the prefix are considered; sessions belonging to
        anything else are never touched.

        Returns the original ``<prefix>:...`` session keys of the files that
        were actually removed, so callers can also evict any in-memory
        bookkeeping (``SessionManager`` cache, active tasks, session locks)
        keyed by the same value — deleting the on-disk file alone leaves those
        caches growing forever.
        """
        files = sorted(
            sessions_dir.glob(f"{prefix}_*.jsonl"), key=lambda p: p.stat().st_mtime,
        )
        if len(files) <= keep:
            return []

        to_remove = files[: len(files) - keep]
        removed_keys: list[str] = []
        for path in to_remove:
            try:
                path.unlink()
                logger.debug("Pruned old {} session: {}", prefix, path.stem)
                removed_keys.append(path.stem.replace("_", ":", 1))
            except OSError:
                logger.warning("Failed to prune {} session {}", prefix, path)
        return removed_keys

    @classmethod
    def prune_dream_sessions(cls, sessions_dir: Path, *, keep: int = 10) -> list[str]:
        """Remove the oldest Dream session files, keeping only the N most recent."""
        return cls.prune_internal_sessions(sessions_dir, "dream", keep=keep)


# ---------------------------------------------------------------------------
# Consolidator — lightweight token-budget triggered consolidation
# ---------------------------------------------------------------------------

# Individual history.jsonl writers cap their own payloads tightly; the
# _HISTORY_ENTRY_HARD_CAP at append_history() is a belt-and-suspenders default
# that catches any new caller that forgot to set its own cap.
_RAW_ARCHIVE_MAX_CHARS = 16_000       # fallback dump (LLM failed)
_ARCHIVE_SUMMARY_MAX_CHARS = 8_000    # LLM-produced consolidation summary
_HISTORY_ENTRY_HARD_CAP = 64_000      # emergency cap in append_history


# Consolidator vive in ``consolidator.py`` (importa MemoryStore qui sopra).
# Re-export in coda per preservare l'API storica; a questo punto MemoryStore
# e le costanti sono già definite, quindi l'import di ritorno non trova un
# modulo a metà inizializzazione.
from jenny.agent.consolidator import Consolidator as Consolidator  # noqa: E402
