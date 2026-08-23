"""Il cursore del giardiniere, e il delta di diario che ne esce.

Passo **T4.1** di ``roadmap/taccuino-passi.md``: la metà del giardiniere che non
chiama nessun modello. Risponde a una domanda sola — *«di questo diario, cosa non
ho ancora letto?»* — e tiene su disco la risposta.

Lo stato vive in ``<progetto>/.jenny/gardener.json``. Il posto non è nuovo: la
cartella nascosta di un progetto ospita già i risultati dei tool
(``.jenny/tool-results/``, v. ``session/project_rename.py``), e sta **fuori da
tutto** senza che nessuno debba impararlo — ``iter_wiki_sources`` cammina solo
``wiki/``, quindi fuori dall'impronta di Atlas, e l'inventario della rubrica
salta i file nascosti. Il quaderno è materiale umano, il cursore è macchinario.

**Righe, non byte.** Un conteggio di righe è significativo *perché* il diario è
append-only, e sopravvive a un editor che riscrive la coda del file; un offset in
byte no. E il conteggio è di righe **fisiche**: quel che si promuove sono le voci,
ma quel che si conta è il file, così il cursore resta una cosa che si verifica
con ``wc -l``.

**Perso il cursore si rilegge da capo.** Non è un caso da evitare, è il
comportamento: lo stato è una cache di lavoro, e la correttezza sta
nell'idempotenza della passata (ripassare le stesse righe non duplica pagine),
non nella durabilità di questo file. Da cui il tetto: una rilettura da zero su
mesi di diario non deve poter diventare un prompt da diecimila righe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from jenny.utils.path import atomic_write
from jenny.utils.wiki_paths import wiki_journal_dir

# Lo stato, relativo alla radice del progetto.
GARDENER_STATE_REL = ".jenny/gardener.json"

_STATE_VERSION = 1

# Quante voci di diario può portarsi una passata. Il caso che questo tetto
# difende non è il diario di una giornata parlante — sono venti righe — ma la
# rilettura da capo dopo un cursore perso: mesi di diario in un prompt solo.
# Duecento voci sono una settimana molto densa, e quel che resta torna al giro
# dopo (v. ``JournalDelta.left_behind``, che il chiamante *deve* dire nel prompt:
# troncare zitti è il difetto che questo ramo ha già pagato due volte).
MAX_DELTA_LINES = 200


@dataclass(frozen=True)
class JournalFileDelta:
    """Le voci non ancora lette di **un** giorno di diario."""

    path: str
    """Percorso relativo alla radice del progetto, POSIX: ``raw/journal/20260822.md``."""

    lines: tuple[str, ...]
    """Le voci, già ripulite: nessuna riga vuota, nessuna intestazione."""

    cursor_after: int
    """Righe fisiche del file consumate da questo delta — il cursore che ne esce."""


@dataclass(frozen=True)
class JournalDelta:
    """Quel che una passata ha da leggere, in ordine cronologico."""

    files: tuple[JournalFileDelta, ...] = ()
    left_behind: int = 0
    """Voci che il tetto ha lasciato fuori. Va **detto**, non ingoiato."""

    @property
    def line_count(self) -> int:
        return sum(len(f.lines) for f in self.files)

    @property
    def is_empty(self) -> bool:
        return not self.files

    def cursor(self) -> dict[str, int]:
        """Il cursore che questo delta produce, una volta consumato."""
        return {f.path: f.cursor_after for f in self.files}


@dataclass(frozen=True)
class GardenerState:
    """Fin dove il diario di un progetto è stato letto, e quando."""

    cursor: dict[str, int] = field(default_factory=dict)
    last_run_at: str | None = None

    def advanced(self, delta: JournalDelta, *, at: datetime | None = None) -> "GardenerState":
        """Lo stesso stato con *delta* consumato.

        Il cursore si **fonde**, non si sostituisce: un delta tocca i giorni che
        hanno righe nuove, e gli altri restano fin dove erano.
        """
        merged = dict(self.cursor)
        merged.update(delta.cursor())
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        return GardenerState(cursor=merged, last_run_at=stamp)

    def payload(self) -> dict[str, object]:
        return {
            "version": _STATE_VERSION,
            "cursor": dict(sorted(self.cursor.items())),
            "last_run_at": self.last_run_at,
        }


def gardener_state_file(root: Path) -> Path:
    """Lo stato del giardiniere per *root*. Non garantisce che esista."""
    return root / GARDENER_STATE_REL


def read_state(root: Path) -> GardenerState:
    """Lo stato su disco, o uno stato vuoto.

    Uno stato illeggibile — troncato, JSON invalido, scritto da una versione che
    non conosciamo — vale **stato vuoto**, cioè rilettura da capo. È la scelta
    giusta perché l'unico costo è ripassare righe già viste, che l'idempotenza
    della passata rende innocuo, mentre indovinare un cursore da un file rotto
    salterebbe righe in silenzio.
    """
    path = gardener_state_file(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return GardenerState()
    if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
        if data:
            logger.warning("gardener: stato non riconosciuto in {}, si rilegge da capo", path)
        return GardenerState()
    raw = data.get("cursor")
    cursor = {
        key: value
        for key, value in (raw.items() if isinstance(raw, dict) else ())
        if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        and value >= 0
    }
    stamp = data.get("last_run_at")
    return GardenerState(cursor=cursor, last_run_at=stamp if isinstance(stamp, str) else None)


def write_state(root: Path, state: GardenerState) -> None:
    """Salva lo stato, potato dei giorni che non esistono più.

    ``atomic_write`` e non ``write_text`` per la stessa ragione di Atlas: uno
    stato troncato a metà si rilegge come JSON invalido, cioè cursore perso, cioè
    una rilettura da capo che nessuno ha chiesto.

    **Si pota solo quel che non c'è più.** La tentazione era potare per età — una
    voce al giorno per sempre *sembra* una perdita — ed è sbagliata: buttare la
    voce di un giorno di diario che esiste ancora significa rileggerlo, cioè
    pagare per la pulizia. Un progetto usato ogni giorno per tre anni tiene mille
    voci, che sono una quarantina di kilobyte: meno di qualunque meccanismo che
    rischi una rilettura.
    """
    pruned = {rel: seen for rel, seen in state.cursor.items() if (root / rel).is_file()}
    if len(pruned) != len(state.cursor):
        logger.debug(
            "gardener: potate {} voci di cursore senza file", len(state.cursor) - len(pruned)
        )
    payload = GardenerState(cursor=pruned, last_run_at=state.last_run_at).payload()
    atomic_write(
        gardener_state_file(root), json.dumps(payload, ensure_ascii=False, indent=2)
    )


def read_journal_delta(
    root: Path,
    state: GardenerState,
    *,
    max_lines: int = MAX_DELTA_LINES,
) -> JournalDelta:
    """Le voci di diario di *root* che *state* dichiara non lette.

    In ordine cronologico, che qui è l'ordine alfabetico dei nomi
    (``AAAAMMGG.md``) — è la ragione per cui il nome del file è fatto così.
    """
    journal = wiki_journal_dir(root)
    if not journal.is_dir():
        return JournalDelta()

    budget = max(0, max_lines)
    files: list[JournalFileDelta] = []
    left_behind = 0

    for page in sorted(journal.glob("*.md")):
        if page.name.startswith("."):
            continue
        rel = page.relative_to(root).as_posix()
        try:
            physical = page.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            # ``UnicodeDecodeError`` **non** e' un ``OSError``, ed e' l'eccezione
            # piu' probabile delle due: un diario e' testo scritto da un modello e
            # copiato a mano da mezzo mondo. Senza questo ramo un solo file mal
            # codificato non saltava una pagina — faceva cadere la passata intera,
            # e quindi congelava il giardiniere su quel progetto per sempre.
            logger.warning("gardener: diario illeggibile {}: {}", rel, exc)
            continue

        seen = state.cursor.get(rel, 0)
        if seen >= len(physical):
            if seen > len(physical):
                # Il file si è accorciato: qualcuno ha riscritto un diario, che
                # l'append-only vieta. Non si rilegge da capo — rileggere
                # ripromuoverebbe roba già promossa — e non si tace: il lint
                # (T5) è il posto che deve trovarlo, questo è il posto che lo
                # racconta.
                logger.warning(
                    "gardener: {} è più corto del cursore ({} righe, cursore {}): "
                    "l'append-only del diario è stato violato",
                    rel, len(physical), seen,
                )
            continue

        taken: list[str] = []
        consumed = seen
        stop = len(physical)
        for index in range(seen, len(physical)):
            line = physical[index].strip()
            if not line or line.startswith("#"):
                # Vuoto e intestazione non sono voci: si consumano senza
                # spendere budget e senza finire nel prompt.
                consumed = index + 1
                continue
            if not budget:
                # **Si esce**, non si continua a scorrere. Continuando, una riga
                # vuota dopo questo punto avrebbe fatto avanzare ``consumed``
                # oltre una voce non letta: quella voce sarebbe finita sotto il
                # cursore senza essere mai stata promossa, cioè persa in
                # silenzio. Il tetto deve fermare la lettura, non filtrarla.
                stop = index
                break
            taken.append(line)
            consumed = index + 1
            budget -= 1

        left_behind += sum(
            1 for raw in physical[stop:] if raw.strip() and not raw.strip().startswith("#")
        )

        if taken:
            files.append(JournalFileDelta(path=rel, lines=tuple(taken), cursor_after=consumed))

    delta = JournalDelta(files=tuple(files), left_behind=left_behind)
    if left_behind:
        logger.info(
            "gardener: delta di {} tagliato a {} voci, {} restano al giro dopo",
            root.name, delta.line_count, left_behind,
        )
    return delta
