"""Creazione di un progetto dalla WebUI: una wiki nuova, completa e vuota.

**Un progetto e' una wiki** (v. `roadmap/project-sessions.md`): non esiste una
`projects/` separata, quindi "nuovo progetto" vuol dire scaffoldare
`wikis/<nome>/` e registrarlo in `wikis/_index.md`. Prima di questo modulo il
chip creava una cartella nuda con `/api/workspace/mkdir`: nessun albero, nessun
file di istruzioni, nessuna voce nel registro — una wiki rotta che sembrava un
progetto.

**Lo scaffolder e' `project_scaffold.py`, nel package** *(dal 22/08, passo T1 di
`roadmap/taccuino-passi.md`)*. Fino a quel giorno era `scaffold.py` della skill,
per non avere due scaffolder che divergono; ora sono due di proposito, perche'
costruiscono **due formati diversi**: la skill fa la biblioteca di ricerca
(`raw/papers`, `concepts|entities|summaries`, le cinque operazioni), e resta la
strada per crearne una chiedendola a Jenny; il picker della UI fa un progetto —
pagine piatte, un diario, la mappa. Il confine e' netto e nessun file e'
conteso.

**La riga dell'utente non e' un extra.** *"Quando crei il progetto devi scrivere
tu qualcosa, sennò la chat è ferma"*: senza una riga di scope il primo turno non
ha su cosa appoggiarsi, e uno scope indovinato dall'agente e' peggio di nessuno
scope, perche' tutto quel che viene archiviato dopo lo eredita. Finisce in due
posti — il `summary:` dell'`AGENTS.md`, da cui `reindex_wikis` costruisce la riga
del registro, e la mappa del progetto, che e' quel che l'agente legge per primo —
e ci entra **alla nascita**, non per sostituzione di un segnaposto dopo.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.webui.project_scaffold import scaffold_project

_TITLE_SPLIT_RE = re.compile(r"[-_.]+")


class ProjectCreateError(Exception):
    """Creazione non riuscita, con un messaggio mostrabile all'utente."""


def project_title(name: str) -> str:
    """Titolo leggibile da un nome di cartella: ``patreon-creator`` -> ``Patreon Creator``.

    Il titolo finisce negli H1 del template e non e' un identificatore: l'utente
    lo puo' correggere nell'`AGENTS.md`, il nome della cartella no (e' l'indirizzo
    della wiki). Per questo lo deriviamo invece di chiederlo: un campo in piu' nel
    dialogo per un dato che si cambia dopo.
    """
    words = [w for w in _TITLE_SPLIT_RE.split(name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or name


def _yaml_scalar(value: str) -> str:
    """*value* come scalare YAML su una riga, sempre valido.

    Perche' quotare: la riga di scope e' testo libero dell'utente e finisce
    *dentro* la frontmatter. Un due punti in mezzo — "Prova del passo 7: la chat
    segue" — rende il blocco YAML non parsabile, e allora si perde **tutta** la
    frontmatter, non solo quella riga. Visto sul telefono il 22/08 su una wiki
    appena creata: ``read_wiki_scope`` cadeva sul ripiego e l'id risultava assente.

    Non si usa ``yaml.dump``: quello aggiunge il documento e a volte manda a
    capo. Qui basta la regola delle virgolette doppie — raddoppiare i backslash,
    scappare le virgolette — che rende sicuro qualunque testo, due punti e
    cancelletti compresi.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def create_project(
    *,
    wikis_dir: Path,
    scripts_dir: Path,
    name: str,
    seed: str,
) -> dict[str, Any]:
    """Scaffolda `wikis_dir/name`, ci scrive la riga di scope, aggiorna il registro.

    Sincrona e con I/O: il chiamante la mette in un thread. Il nome arriva gia'
    validato — questo modulo non e' il gate.
    """
    root = wikis_dir / name
    if root.exists():
        raise ProjectCreateError(f"project already exists: {name}")

    try:
        created = scaffold_project(root, project_title(name), seed, _yaml_scalar(seed))
    except Exception as exc:
        raise ProjectCreateError(f"scaffold failed: {exc}") from exc

    # Il registro lo scrive il chiamante e non lo scaffolder: quello conosce una
    # cartella, questo conosce `wikis_dir`. `reindex_wikis` resta della skill —
    # e' il registro del *workspace*, comune ai due formati, e non c'e' niente da
    # duplicare.
    registry: str | None = None
    try:
        reindex = importlib.util.spec_from_file_location(
            "reindex_wikis", scripts_dir / "reindex_wikis.py"
        )
        if reindex is not None and reindex.loader is not None:
            module = importlib.util.module_from_spec(reindex)
            reindex.loader.exec_module(module)
            with contextlib.redirect_stdout(io.StringIO()):
                registry = str(module.regenerate_index(wikis_dir))
    except Exception as exc:
        # La wiki esiste ed e' completa: un registro non aggiornato e' un
        # inconveniente che `lint --workspace` sa riparare, non un fallimento
        # della creazione.
        logger.warning("could not refresh {} after seeding: {}", wikis_dir, exc)

    return {
        "name": name,
        "created": list(created or []),
        # La riga di scope entra alla nascita, quindi c'e' sempre: il campo resta
        # nella risposta perche' il client lo legge, e resta `True` invece di
        # sparire per non cambiare la forma di un payload per un dettaglio
        # interno.
        "seeded": True,
        "registry": registry,
    }
