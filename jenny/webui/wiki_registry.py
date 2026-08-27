"""Rigenerare ``wikis/_index.md``, il registro delle wiki del workspace.

Estratto da :mod:`jenny.webui.project_create` quando la cancellazione di un
progetto e' diventata un'operazione vera: creare e cancellare devono lasciare il
registro nello stesso stato, e due copie di questa funzione sarebbero due
occasioni di divergere.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from loguru import logger


def refresh_wiki_registry(wikis_dir: Path, scripts_dir: Path) -> str | None:
    """Riscrive il registro e ne ritorna il percorso, ``None`` se non c'e' riuscito.

    **Non solleva mai.** Il registro e' derivato: si ricostruisce dalle cartelle,
    quindi restare indietro di un giro non perde niente e non deve poter far
    fallire l'operazione che l'ha chiamata.
    """
    registry: str | None = None
    try:
        reindex = importlib.util.spec_from_file_location(
            "reindex_wikis", scripts_dir / "reindex_wikis.py"
        )
        if reindex is not None and reindex.loader is not None:
            module = importlib.util.module_from_spec(reindex)
            reindex.loader.exec_module(module)
            # **Niente ``redirect_stdout`` qui.** Questa funzione gira dentro un
            # ``asyncio.to_thread`` (v. ``webui/commands.py``), e
            # ``redirect_stdout`` muta ``sys.stdout`` **di processo**: per tutta
            # la finestra, l'output di *ogni altro* thread finisce nel buffer che
            # buttiamo via. Non è teorico: ``python_exec`` cattura quel che il
            # codice del modello stampa proprio via ``sys.stdout``, e ha
            # sostituito il proprio ``redirect_stdout`` con un proxy per-thread
            # esattamente per questo (v. il commento lungo in
            # ``agent/tools/python_exec.py``, «cattura di stdout PER THREAD»).
            # Il proxy però si consulta al momento della scrittura, e chi scrive
            # legge ``sys.stdout``: con la nostra ``StringIO`` al suo posto, un
            # ``print()`` del modello nel turno accanto sparisce in silenzio.
            #
            # E non c'era niente da nascondere: ``regenerate_index`` stampa una
            # riga sola, su **stderr** (il caso «_index.md senza marcatori»), che
            # un ``redirect_stdout`` non tocca nemmeno. Costo del silenzio: zero.
            # Rischio: l'output di un altro turno.
            registry = str(module.regenerate_index(wikis_dir))
    except Exception as exc:
        # Chi ha chiamato ha gia' fatto la sua parte sul disco: un registro
        # non aggiornato e' un inconveniente che `lint --workspace` sa
        # riparare, non un fallimento dell'operazione.
        logger.warning("could not refresh the registry in {}: {}", wikis_dir, exc)
    return registry
