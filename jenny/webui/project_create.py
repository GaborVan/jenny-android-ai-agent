"""Creazione di un progetto dalla WebUI: una wiki nuova, completa e vuota.

**Un progetto e' una wiki** (v. `roadmap/project-sessions.md`): non esiste una
`projects/` separata, quindi "nuovo progetto" vuol dire scaffoldare
`wikis/<nome>/` e registrarlo in `wikis/_index.md`. Prima di questo modulo il
chip creava una cartella nuda con `/api/workspace/mkdir`: nessun albero, nessun
`CLAUDE.md`, nessuna voce nel registro — una wiki rotta che sembrava un
progetto.

**Lo scaffolder e' quello della skill, non una copia.** `scaffold.py` vive in
`workspace/skills/llm-wiki/scripts/`, e' un checkout che l'utente puo'
modificare, ed e' la fonte di verita' sulla forma dei file: reimplementarne
l'albero qui vorrebbe dire avere due scaffolder che divergono al primo cambio
della skill. Lo carichiamo come fa il builtin `wiki_scaffold` di `python_exec`
(``agent/tools/python_exec_builtins.py``), che e' l'altro chiamante.

**La riga dell'utente non e' un extra.** *"Quando crei il progetto devi scrivere
tu qualcosa, sennò la chat è ferma"*: senza una riga di scope il primo turno non
ha su cosa appoggiarsi, e uno scope indovinato dall'agente e' peggio di nessuno
scope, perche' tutto quel che viene archiviato dopo lo eredita. Finisce nel
`summary:` del `CLAUDE.md`, che e' il campo da cui `reindex_wikis` costruisce la
riga del registro.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
from pathlib import Path
from types import ModuleType
from typing import Any

from loguru import logger

# I due placeholder del template di `scaffold.py` in cui va la riga dell'utente.
# Sostituiti *solo se presenti*: se l'utente ha riscritto il template, un valore
# vero non va sovrascritto — la stessa regola che rende sicuro il top-up dello
# scaffold.
_SUMMARY_PLACEHOLDER = "<one-line scope — shown next to this wiki in wikis/_index.md>"
_SCOPE_PLACEHOLDER = "- <describe the topic area>"

_SCHEMA_FILENAME = "CLAUDE.md"

_TITLE_SPLIT_RE = re.compile(r"[-_.]+")


class ProjectCreateError(Exception):
    """Creazione non riuscita, con un messaggio mostrabile all'utente."""


def project_title(name: str) -> str:
    """Titolo leggibile da un nome di cartella: ``patreon-creator`` -> ``Patreon Creator``.

    Il titolo finisce negli H1 del template e non e' un identificatore: l'utente
    lo puo' correggere nel `CLAUDE.md`, il nome della cartella no (e' l'indirizzo
    della wiki). Per questo lo deriviamo invece di chiederlo: un campo in piu' nel
    dialogo per un dato che si cambia dopo.
    """
    words = [w for w in _TITLE_SPLIT_RE.split(name) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words) or name


def load_scaffold_script(scripts_dir: Path) -> ModuleType:
    """Carica `scaffold.py` dal checkout della skill nel workspace.

    Nessun fallback su `exec()` come in `python_exec_builtins`: qui l'errore ha
    un utente davanti e un toast in cui stare, quindi e' meglio dirgli che lo
    script non c'e' (o non compila) che eseguirne una versione caricata per
    un'altra strada.
    """
    script_path = scripts_dir / "scaffold.py"
    if not script_path.is_file():
        raise ProjectCreateError(f"scaffold script not found: {script_path}")
    spec = importlib.util.spec_from_file_location("scaffold", script_path)
    if spec is None or spec.loader is None:
        raise ProjectCreateError(f"scaffold script not loadable: {script_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProjectCreateError(f"scaffold script failed to load: {exc}") from exc
    return module


def _seed_schema_file(wiki_root: Path, seed: str) -> bool:
    """Mette *seed* nei placeholder del `CLAUDE.md`. True se ne ha scritto almeno uno."""
    schema = wiki_root / _SCHEMA_FILENAME
    try:
        text = schema.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read {} to seed the scope: {}", schema, exc)
        return False

    seeded = False
    if _SUMMARY_PLACEHOLDER in text:
        text = text.replace(_SUMMARY_PLACEHOLDER, seed, 1)
        seeded = True
    if _SCOPE_PLACEHOLDER in text:
        text = text.replace(_SCOPE_PLACEHOLDER, f"- {seed}", 1)
        seeded = True

    if seeded:
        schema.write_text(text, encoding="utf-8")
    else:
        # Non inventiamo un posto dove metterla: il registro cade sul fallback e
        # l'utente vede la wiki senza riga di scope, che e' visibile e correggibile.
        logger.warning("no scope placeholder left in {} — seed not written", schema)
    return seeded


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

    scaffold_mod = load_scaffold_script(scripts_dir)

    # Lo scaffolder stampa il suo report su stdout: catturato, non perso, cosi'
    # un'anomalia (una wiki che esisteva a metà) resta nei log del gateway.
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            created = scaffold_mod.scaffold(str(root), project_title(name))
    except Exception as exc:
        raise ProjectCreateError(f"scaffold failed: {exc}") from exc
    logger.info("scaffolded project {}:\n{}", name, buffer.getvalue().strip())

    seeded = _seed_schema_file(root, seed)

    # Il registro va rigenerato *dopo* la riga di scope: `scaffold.py` lo scrive
    # da se', ma con il placeholder ancora dentro.
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
        "seeded": seeded,
        "registry": registry,
    }
