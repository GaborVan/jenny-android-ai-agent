"""Chi viene giardinato a questo tick, e i tre orologi che lo decidono.

Passo **T4.3** di ``roadmap/taccuino-passi.md``. Il job cron batte ogni mezz'ora;
questo modulo risponde all'unica domanda che il battito pone: *su quale progetto,
se su nessuno.*

I tre cancelli, e ognuno risponde a una domanda diversa:

1. **Distanza** — sono passate almeno N ore dall'ultima passata su *questa* wiki?
   È la lezione del degrado del Dream scritta come numero, e va per materia
   perché il degrado è per materia.
2. **Fermo** — la conversazione di quel progetto è zitta da N minuti, **e non ha
   un turno in volo adesso**. La seconda metà non è una rifinitura della prima:
   ``run_gardener`` gira su una chiave sua (``gardener:<nome>``) e **non
   condivide il lock** della conversazione del progetto, quindi utente e
   giardiniere possono scrivere la mappa nello stesso momento e l'ultimo che
   salva cancella l'altro. Questo cancello è tutto ciò che tiene i due scrittori
   lontani.
3. **Delta** — ci sono righe di diario non lette?

**In quest'ordine, e l'ordine è il costo.** Distanza e fermo si decidono con due
letture di un file piccolo (lo stato del giardiniere, i metadati di sessione); il
delta vuole aprire i diari. Chiedere prima le cose che costano meno significa
che, in un'installazione con otto progetti fermi, un tick tocca pochi byte.

**Una passata per tick, la meno recente per prima.** Il tetto non è prudenza
astratta: otto progetti con righe nuove farebbero otto turni LLM di fila su un
telefono. Con un tick ogni mezz'ora, gli altri aspettano trenta minuti — che su
un lavoro con sei ore di distanza minima non è un ritardo.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.gardener import GardenerStore
from jenny.agent.gardener_state import read_state
from jenny.session.keys import project_session_key
from jenny.utils.wiki_paths import discover_wiki_roots


@dataclass(frozen=True)
class GardenerPick:
    """Il progetto scelto e il perché, per il log."""

    store: GardenerStore
    delta_lines: int
    last_run_at: str | None


def _age_seconds(stamp: Any, now: datetime) -> float | None:
    """Secondi da *stamp* (ISO), o ``None`` se non è una data leggibile."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds()
    except ValueError:
        return None


def _far_enough(last_run_at: Any, now: datetime, min_hours: int) -> bool:
    """Se è passato abbastanza dall'ultima passata su questa wiki.

    Mai giardinata (nessuno stato, o una data illeggibile) vuol dire **sì**: il
    caso da servire per primo è quello del progetto nuovo, e un cursore perso non
    deve poter bloccare il lavoro per sei ore.
    """
    if min_hours <= 0:
        return True
    age = _age_seconds(last_run_at, now)
    return age is None or age >= min_hours * 3600


def _quiet_enough(sessions: Any, key: str, now: datetime, idle_min: int) -> bool:
    """Se la conversazione di quel progetto è ferma da abbastanza.

    Nessun metadato vuol dire che quella conversazione non è mai esistita, quindi
    non c'è niente che stia parlando: **fermo**. Una data illeggibile vale la
    stessa cosa — l'alternativa sarebbe un progetto che non viene mai giardinato
    per un timestamp corrotto, cioè un guasto che si nasconde.
    """
    if idle_min <= 0:
        return True
    try:
        info = sessions.read_session_metadata(key)
    except Exception:  # noqa: BLE001 — la lettura dei metadati non deve fermare il tick
        logger.warning("gardener: metadati di sessione illeggibili per {}", key)
        return True
    if not isinstance(info, dict):
        return True
    age = _age_seconds(info.get("updated_at"), now)
    return age is None or age >= idle_min * 60


def pick_project(
    workspace: Path,
    *,
    idle_min: int,
    min_hours_between_passes: int,
    sessions: Any,
    active_session_keys: Collection[str] = (),
    wikis_dir_name: str = "wikis",
    now: datetime | None = None,
) -> GardenerPick | None:
    """Il progetto da giardinare adesso, o ``None`` se nessuno è pronto.

    *sessions* è il ``SessionManager`` (serve solo ``read_session_metadata``);
    *active_session_keys* le sessioni con un turno in volo in questo istante.
    """
    at = now or datetime.now()
    candidates: list[GardenerPick] = []

    for name in sorted(discover_wiki_roots(workspace / wikis_dir_name)):
        store = GardenerStore.for_project(workspace, name, wikis_dir_name=wikis_dir_name)
        if store is None:
            continue

        state = read_state(store.root)
        if not _far_enough(state.last_run_at, at, min_hours_between_passes):
            continue

        key = project_session_key(name)
        if key in active_session_keys:
            # Un turno in volo adesso: non è "quasi fermo", è il caso peggiore.
            continue
        if not _quiet_enough(sessions, key, at, idle_min):
            continue

        # Ultimo, perché è l'unico che apre dei file.
        delta = store.read_delta()
        if delta.is_empty:
            continue
        candidates.append(
            GardenerPick(store=store, delta_lines=delta.line_count,
                         last_run_at=state.last_run_at)
        )

    if not candidates:
        return None
    # La meno recente per prima; le mai giardinate prima di tutte (``""`` ordina
    # sotto qualunque data ISO). A pari merito decide il nome, così la scelta è
    # deterministica e un test può fissarla.
    candidates.sort(key=lambda c: (c.last_run_at or "", c.store.name))
    pick = candidates[0]
    if len(candidates) > 1:
        # Detto, non taciuto: gli altri aspettano il tick dopo, e chi legge i log
        # deve poter vedere che non sono stati dimenticati.
        logger.info(
            "gardener: {} progetti pronti, questo tick prende {} ({} righe); gli altri: {}",
            len(candidates), pick.store.name, pick.delta_lines,
            ", ".join(c.store.name for c in candidates[1:]),
        )
    return pick
