"""Shared session key constants and helpers."""

from __future__ import annotations

UNIFIED_SESSION_KEY = "unified:default"

# Prefisso delle sessioni Tier-2 dei subagent (``subagent:<lineage_id>``).
# Sono storia di lavoro interno, non conversazioni: non devono comparire in
# nessun elenco user-facing ne essere leggibili dalle route HTTP della WebUI.
SUBAGENT_SESSION_PREFIX = "subagent:"

# Prefissi delle sessioni interne (lavoro del sistema, non conversazione con
# l'utente). Elencare le sessioni e per definizione un'operazione user-facing:
# chi lo fa deve filtrare con :func:`is_internal_session_key`.
_INTERNAL_SESSION_PREFIXES = (SUBAGENT_SESSION_PREFIX, "cron:", "dream:", "heartbeat:")


def is_internal_session_key(key: str) -> bool:
    """True se la session key appartiene a lavoro interno, non all'utente.

    Usata come filtro unico per gli elenchi di sessioni: il confine sta qui e
    non replicato in ogni chiamante, cosi aggiungere un prefisso interno non
    richiede di ricordarsi di aggiornare N punti.
    """
    return any(key.startswith(prefix) for prefix in _INTERNAL_SESSION_PREFIXES)


def subagent_session_key(lineage_id: str) -> str:
    """Session key della storia Tier-2 di un lineage."""
    return f"{SUBAGENT_SESSION_PREFIX}{lineage_id}"


def session_key_for_channel(channel: str, chat_id: str) -> str:
    """Return the session key for a channel/chat pair.

    Every channel/chat maps onto the single unified conversation; explicit
    ``session_key_override`` values (internal keys) bypass this helper.
    """
    return UNIFIED_SESSION_KEY
