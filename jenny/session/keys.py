"""Shared session key constants and helpers."""

from __future__ import annotations

__all__ = [
    "ATLAS_SESSION_PREFIX",
    "CRON_SESSION_PREFIX",
    "DREAM_SESSION_PREFIX",
    "HEARTBEAT_SESSION_KEY",
    "INTERNAL_SESSION_PREFIX",
    "SUBAGENT_SESSION_PREFIX",
    "UNIFIED_SESSION_KEY",
    "internal_session_kind",
    "is_internal_session_key",
    "is_personal_session_key",
    "session_key_for_channel",
    "subagent_session_key",
]

UNIFIED_SESSION_KEY = "unified:default"

# Prefisso delle sessioni Tier-2 dei subagent (``subagent:<lineage_id>``).
# Sono storia di lavoro interno, non conversazioni: non devono comparire in
# nessun elenco user-facing ne essere leggibili dalle route HTTP della WebUI.
SUBAGENT_SESSION_PREFIX = "subagent:"

# Prefisso dei run di un job cron (``cron:<job_id>``, v. ``cron/session_turns``).
CRON_SESSION_PREFIX = "cron:"

# Prefisso dei run di Dream (``dream:<timestamp>``, v. ``agent/memory``).
DREAM_SESSION_PREFIX = "dream:"

# Prefisso dei run di Atlas (``atlas:<timestamp>``, v. ``agent/atlas``).
ATLAS_SESSION_PREFIX = "atlas:"

# Prefisso del turno interno generico (``internal:direct``): e' il default di
# ``AgentLoop.process_direct``, che oggi nessun chiamante di produzione lascia
# scoperto — tutti passano una chiave esplicita.
INTERNAL_SESSION_PREFIX = "internal:"

# Sessione dell'heartbeat: chiave *nuda*, senza suffisso, perche ce n'e una sola
# (``cron_dispatch._run_heartbeat``). Sta qui e non inline nel dispatcher perche
# e' anche il discriminante di :func:`is_internal_session_key`.
HEARTBEAT_SESSION_KEY = "heartbeat"

# Vocabolario delle sessioni interne (lavoro del sistema, non conversazione con
# l'utente): prefisso -> *kind*. Elencare le sessioni e per definizione
# un'operazione user-facing: chi lo fa deve filtrare con
# :func:`is_internal_session_key`.
#
# Questo modulo e' la porta d'ingresso del vocabolario: chi ha bisogno di
# distinguere le sessioni interne importa da qui invece di ricopiarsi la lista
# dei prefissi, cosi il lato scrittura e il lato lettura non possono divergere.
_INTERNAL_KIND_BY_PREFIX: tuple[tuple[str, str], ...] = (
    (SUBAGENT_SESSION_PREFIX, "subagent"),
    (CRON_SESSION_PREFIX, "cron"),
    (DREAM_SESSION_PREFIX, "dream"),
    (ATLAS_SESSION_PREFIX, "atlas"),
    (INTERNAL_SESSION_PREFIX, "internal"),
)

# Chiavi interne senza suffisso: vanno confrontate per uguaglianza, non per
# prefisso, altrimenti non matchano (era il caso di ``heartbeat``, che il
# prefisso ``"heartbeat:"`` non ha mai intercettato).
_INTERNAL_KIND_BY_KEY: dict[str, str] = {HEARTBEAT_SESSION_KEY: "heartbeat"}


def internal_session_kind(key: str) -> str | None:
    """Il *kind* di lavoro interno a cui appartiene la session key, o ``None``.

    Ritorna una delle etichette del vocabolario (``"subagent"``, ``"cron"``,
    ``"dream"``, ``"atlas"``, ``"internal"``, ``"heartbeat"``) quando la chiave
    e' di una sessione interna, ``None`` quando e' una conversazione utente.

    Serve a chi non ha bisogno solo del si/no di
    :func:`is_internal_session_key` ma anche di *quale* interno sia — per
    esempio per instradare la contabilita dei token su un bucket diverso.
    """
    kind = _INTERNAL_KIND_BY_KEY.get(key)
    if kind is not None:
        return kind
    for prefix, prefix_kind in _INTERNAL_KIND_BY_PREFIX:
        if key.startswith(prefix):
            return prefix_kind
    return None


def is_internal_session_key(key: str) -> bool:
    """True se la session key appartiene a lavoro interno, non all'utente.

    Usata come filtro unico per gli elenchi di sessioni e come default della
    visibilita di un turno (:mod:`jenny.session.turn_visibility`): il confine
    sta qui e non replicato in ogni chiamante, cosi aggiungere una sessione
    interna non richiede di ricordarsi di aggiornare N punti.
    """
    return internal_session_kind(key) is not None


def is_personal_session_key(key: str) -> bool:
    """True se la session key e' la conversazione personale con l'utente.

    Oggi e' l'esatto complemento di :func:`is_internal_session_key`, e il nome
    e' l'unica differenza. Ma e' la differenza che serve: chi decide *cosa
    entra nella memoria di lungo periodo* ha bisogno di una whitelist — "solo
    la conversazione personale" — non della blacklist dei lavori interni. Le
    due coincidono finche' le categorie sono due; il giorno in cui ne esiste
    una terza (una sessione legata a un progetto, che e' conversazione con
    l'utente ma **non** deve alimentare il diario personale) la blacklist
    diventa silenziosamente sbagliata e questa resta giusta cambiando qui, in
    un punto solo, senza toccare nessun chiamante.
    """
    return not is_internal_session_key(key)


def subagent_session_key(lineage_id: str) -> str:
    """Session key della storia Tier-2 di un lineage."""
    return f"{SUBAGENT_SESSION_PREFIX}{lineage_id}"


def session_key_for_channel(channel: str, chat_id: str) -> str:
    """Return the session key for a channel/chat pair.

    Every channel/chat maps onto the single unified conversation; explicit
    ``session_key_override`` values (internal keys) bypass this helper.
    """
    return UNIFIED_SESSION_KEY
