"""Shared session key constants and helpers."""

from __future__ import annotations

__all__ = [
    "ATLAS_SESSION_PREFIX",
    "CRON_SESSION_PREFIX",
    "DREAM_SESSION_PREFIX",
    "HEARTBEAT_SESSION_KEY",
    "INTERNAL_SESSION_PREFIX",
    "PROJECT_SESSION_PREFIX",
    "SUBAGENT_SESSION_PREFIX",
    "UNIFIED_SESSION_KEY",
    "internal_session_kind",
    "is_internal_session_key",
    "is_personal_session_key",
    "is_project_session_key",
    "normalize_user_session_key",
    "project_session_key",
    "session_key_for_channel",
    "session_kind",
    "subagent_session_key",
]

UNIFIED_SESSION_KEY = "unified:default"

# Prefisso di una sessione-progetto (``project:<id>``): una conversazione con
# l'utente, legata a una cartella, che **non** alimenta la memoria di lungo
# periodo. E' la terza categoria, e la ragione per cui questo modulo ha una
# classificazione ternaria invece di un booleano: v. :func:`session_kind`.
#
# L'id e' stabile ai rinomini della cartella (la cartella e' l'indirizzo, l'id
# e' l'identita'), quindi non e' derivabile dal path: lo assegna chi crea il
# legame.
PROJECT_SESSION_PREFIX = "project:"

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


def session_kind(key: str) -> str:
    """La categoria di una session key: ``"internal"``, ``"project"``, ``"personal"``.

    **La classificazione delle sessioni sta qui e in nessun altro posto.** Le tre
    categorie non sono una tassonomia per bellezza: rispondono a domande diverse
    e vengono trattate diversamente da chi tiene la memoria.

    - ``internal`` — lavoro del sistema (cron, Dream, Atlas, subagent,
      heartbeat). Non e' conversazione, non compare negli elenchi user-facing, e
      rilegge le *proprie* voci in coda di lavoro perche' e' cosi che un job si
      ricorda dei suoi run passati.
    - ``project`` — conversazione con l'utente dentro un progetto. Non alimenta
      la memoria di lungo periodo e non condivide niente della contabilita
      personale: ne' la coda, ne' il cursore. La sua continuita vive nella
      propria sessione e nei file che scrive.
    - ``personal`` — la conversazione con l'utente. E' la sola che alimenta il
      diario da cui Dream costruisce ``MEMORY.md``.

    Il predicato che serviva prima era binario, e :func:`is_personal_session_key`
    aveva scritto in docstring che il giorno in cui fosse nata una terza
    categoria la blacklist "non e' interna" sarebbe diventata silenziosamente
    sbagliata. Quel giorno e' questo: una chiave ``project:`` non e' interna, e
    con la vecchia definizione risultava percio' *personale* — cioe' avrebbe
    alimentato ``MEMORY.md``. Le tre etichette qui sono un insieme chiuso; chi ne
    ha bisogno usa i tre predicati sotto e non riscrive il confronto.
    """
    if internal_session_kind(key) is not None:
        return "internal"
    if key.startswith(PROJECT_SESSION_PREFIX):
        return "project"
    return "personal"


def is_internal_session_key(key: str) -> bool:
    """True se la session key appartiene a lavoro interno, non all'utente.

    Usata come filtro unico per gli elenchi di sessioni e come default della
    visibilita di un turno (:mod:`jenny.session.turn_visibility`): il confine
    sta qui e non replicato in ogni chiamante, cosi aggiungere una sessione
    interna non richiede di ricordarsi di aggiornare N punti.
    """
    return session_kind(key) == "internal"


def is_project_session_key(key: str) -> bool:
    """True se la session key e' la conversazione di un progetto."""
    return session_kind(key) == "project"


def is_personal_session_key(key: str) -> bool:
    """True se la session key e' la conversazione personale con l'utente.

    **Whitelist, non la negazione di :func:`is_internal_session_key`.** Chi la usa
    decide cosa entra nella memoria di lungo periodo, e per quella decisione
    serve l'elenco di chi *puo'* — che oggi ha un solo membro — e non quello di
    chi non puo'. Da quando esiste :func:`session_kind` le due non coincidono
    piu': una chiave ``project:`` non e' interna e non e' personale.
    """
    return session_kind(key) == "personal"


def project_session_key(project_id: str) -> str:
    """La session key di un progetto dal suo id.

    Sta qui accanto al prefisso per la stessa ragione di
    :func:`subagent_session_key`: la chiave la compone chi la definisce, cosi il
    lato che la scrive e quello che la classifica non possono divergere.
    """
    return f"{PROJECT_SESSION_PREFIX}{project_id}"


# Chiavi utente nella forma vecchia ``<canale>:<chat_id>``. Non esistono piu' come
# sessioni: le scriveva ``CronTool.set_context`` nei payload dei job, quindi si
# incontrano solo rileggendo un ``jobs.json`` scritto prima della sessione unica.
#
# **Elenco chiuso, e non un pattern.** Un pattern "``<parola>:<parola>``"
# prenderebbe anche ``project:<id>``, che e' una sessione vera: collassarla sulla
# conversazione personale farebbe girare un job di progetto nella chat personale,
# cioe' esattamente la confusione che le sessioni-progetto esistono per evitare.
_LEGACY_CHANNEL_KEY_PREFIXES: tuple[str, ...] = ("websocket:", "telegram:")


def normalize_user_session_key(key: str) -> str:
    """La session key persistita, riportata alla forma corrente.

    Serve a un solo punto — il caricamento dello store dei job cron — e vive qui
    perche' la forma delle chiavi la decide questo modulo. Tutto cio' che non e'
    una chiave utente legacy torna identico: le chiavi interne, la conversazione
    unica e qualunque categoria futura passano intatte.
    """
    if key.startswith(_LEGACY_CHANNEL_KEY_PREFIXES):
        return UNIFIED_SESSION_KEY
    return key


def subagent_session_key(lineage_id: str) -> str:
    """Session key della storia Tier-2 di un lineage."""
    return f"{SUBAGENT_SESSION_PREFIX}{lineage_id}"


def session_key_for_channel(channel: str, chat_id: str) -> str:
    """Return the session key for a channel/chat pair.

    Every channel/chat maps onto the single unified conversation; explicit
    ``session_key_override`` values (internal keys) bypass this helper.
    """
    return UNIFIED_SESSION_KEY
