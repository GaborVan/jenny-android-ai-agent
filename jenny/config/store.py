"""Funnel unico per le modifiche a `config.json`.

Ogni scrittura della config era un leggi-modifica-riscrivi indipendente, e
``save_config`` riscrive il file intero: chi aveva letto prima e salvava dopo
cancellava in silenzio la modifica di un altro. Il caso concreto era il
pairing Telegram, che teneva un ``Config`` in mano attraverso tre chiamate di
rete prima di salvare — secondi in cui qualunque altra impostazione toccata
dalla WebUI veniva riportata indietro.

Qui la lettura avviene *dentro* il lock, subito prima della modifica, così una
copia vecchia non può più esistere. Chi deve fare I/O lento lo fa **prima** di
entrare in :func:`mutate`.

Le primitive di fedeltà del file (atomicità, backup, conservazione delle chiavi
ignote) stanno in :mod:`jenny.config.loader`; questo modulo le orchestra.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from jenny.config.loader import load_config_with_raw, save_config
from jenny.config.schema import CURRENT_CONFIG_VERSION, Config

# Un lock di processo: tutte le scritture della config passano dall'event loop
# del gateway. Le due eccezioni documentate (bootstrap pre-loop e ripristino da
# backup) non ne hanno bisogno e usano ``save_config`` direttamente.
_LOCK = asyncio.Lock()


async def mutate(
    apply: Callable[[Config], bool | None],
    *,
    config_path: Path | None = None,
) -> Config:
    """Applica *apply* alla config e la salva, serializzando con gli altri scrittori.

    *apply* riceve la config appena letta da disco e la modifica sul posto; non
    deve fare I/O lento (rete, sottoprocessi): il lock resta preso per tutta la
    sua durata. Se solleva, non viene scritto niente.

    Restituendo ``False`` dichiara che non c'è nulla da cambiare e il file non
    viene toccato — così un GET-che-non-modifica non riscrive `config.json` né
    ruota il backup. Qualsiasi altro valore (incluso ``None``) salva.

    Restituisce la config risultante, già pronta per il payload di risposta.
    """
    async with _LOCK:
        config, raw = load_config_with_raw(config_path)
        if apply(config) is False:
            return config
        save_config(config, config_path, preserve_unknown_from=raw)
        return config


async def persist_schema_migrations(*, config_path: Path | None = None) -> bool:
    """Persiste lo stamp di ``configVersion`` se il file è indietro. ``True`` se ha scritto.

    Le migrazioni di :meth:`Config._migrate_by_version` sono in-memory: valgono
    subito, ma finché il file non viene riscritto ripartono a ogni parse — e il
    config viene letto più volte per boot, quindi il warning si moltiplica e lo
    stamp potrebbe non arrivare mai se l'utente non cambia mai un'impostazione.
    Una scrittura sola all'avvio chiude il ciclo.

    Va chiamata a gateway avviato, non dal bootstrap: passa da :func:`mutate`,
    che vuole l'event loop.
    """
    raw_version = 0
    try:
        _, raw = load_config_with_raw(config_path)
        candidate = raw.get("configVersion", raw.get("config_version", 0))
        raw_version = int(candidate)
    except Exception:
        # File assente, illeggibile o versione non numerica: in tutti i casi
        # "indietro". Il rewrite lo sistema; se non si può leggere, mutate
        # fallirà con il suo errore, non con uno nostro.
        raw_version = -1
    if raw_version >= CURRENT_CONFIG_VERSION:
        return False
    await mutate(lambda _cfg: None, config_path=config_path)
    logger.info("Config schema stamped at version {}", CURRENT_CONFIG_VERSION)
    return True


def locked() -> bool:
    """True se una mutazione è in corso (usato dai test)."""
    return _LOCK.locked()
