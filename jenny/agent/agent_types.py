"""Registry dei tipi di agente: prompt + allowlist di tool + default.

Un "tipo" e la sola cosa che distingue un subagent da un altro: il prompt di
ruolo, l'insieme di tool che puo vedere e i default di campionamento. Vive in un
modulo foglia (nessun import da ``jenny.agent.*``) perche lo leggono tre livelli
diversi — :class:`~jenny.agent.subagent_records.SubagentSpec` per validare,
:class:`~jenny.agent.subagent.SubagentManager` per costruire il registry dei
tool, e il tool ``spawn`` per pubblicarne l'enum al modello.

La separazione fra i tipi non e decorativa: ``researcher`` legge il web e NON
puo eseguire codice, ``writer`` scrive testo e NON ha rete. Chi ha letto pagine
non fidate non e lo stesso che poi esegue codice — e un confine di sicurezza, e
allargare una di queste due allowlist lo cancella.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

# Tipo di fallback: usato quando il chiamante non ne indica uno e quando un
# record vecchio su disco porta un tipo che non esiste piu.
DEFAULT_AGENT_TYPE = "operator"


class UnknownAgentTypeError(ValueError):
    """Tipo di agente inesistente, sollevata alla costruzione della spec.

    Il messaggio elenca i tipi validi: il chiamante tipico e un LLM che si e
    inventato il nome, e un errore senza l'elenco lo fa solo indovinare di
    nuovo.
    """

    def __init__(self, name: object) -> None:
        self.requested = name
        super().__init__(
            f"unknown agent_type {name!r}: valid types are "
            + ", ".join(AGENT_TYPE_NAMES)
        )


@dataclass(frozen=True, slots=True)
class AgentType:
    """Definizione di un tipo di agente.

    ``tools`` e l'allowlist per nome passata a ``ToolLoader.load(allow=...)``;
    ``None`` significa "tutto lo scope subagent" e non e la stessa cosa di un
    set vuoto. ``temperature``/``max_iterations``/``model`` sono default: se
    ``None`` vale quello del manager, e un valore esplicito passato allo spawn
    vince sempre su quello del tipo.
    """

    name: str
    tools: frozenset[str] | None
    temperature: float | None = None
    max_iterations: int | None = None
    model: str | None = None

    @property
    def prompt_template(self) -> str:
        """Template del prompt di ruolo, incluso nel base ``subagent_system.md``."""
        return f"agent/types/{self.name}.md"


# Insieme dei tool del filesystem "scrivibile" (il modulo filesystem completo).
_FS_ALL = ("read_file", "write_file", "edit_file", "list_dir")
_FS_READ = ("read_file", "list_dir")

_AGENT_TYPES: tuple[AgentType, ...] = (
    # Raccoglie materiale dal web. Nessuna esecuzione di codice: e il tipo piu
    # esposto a contenuto non fidato, e un web fetch che finisce in python_exec
    # e il percorso piu corto da "pagina ostile" a "codice eseguito".
    AgentType(
        name="researcher",
        tools=frozenset(("web_search", "web_fetch", *_FS_READ, "write_file")),
        temperature=0.2,
        max_iterations=60,
    ),
    # Sintesi/wiki/docs da materiale gia raccolto: nessuna rete, ne diretta
    # (web_*) ne indiretta (download_file, python_exec). Chi scrive il
    # documento finale non deve poter andare a ripescare la fonte non fidata.
    AgentType(
        name="writer",
        tools=frozenset((*_FS_READ, "write_file", "apply_patch")),
        temperature=0.5,
        max_iterations=40,
    ),
    # Scrive e modifica codice: filesystem completo, patch, esecuzione, sessioni
    # exec e log. ``find_files``/``grep`` sono inclusi deliberatamente (vedi
    # report di fase): senza ricerca un coder reimplementa grep in python_exec,
    # con output peggiore.
    AgentType(
        name="coder",
        tools=frozenset((
            *_FS_ALL,
            "apply_patch",
            "python_exec",
            "list_exec_sessions",
            "write_stdin",
            "get_recent_logs",
            "find_files",
            "grep",
        )),
        temperature=0.1,
        max_iterations=120,
    ),
    # Calcolo, dati, grafici. Nessuna rete: gira codice, quindi non deve poter
    # anche scaricare cio che esegue.
    AgentType(
        name="analyst",
        tools=frozenset(("python_exec", *_FS_READ, "write_file")),
        temperature=0.1,
        max_iterations=60,
    ),
    # Fallback per cio che non rientra negli altri: l'intero scope ``subagent``,
    # cioe esattamente il subagent generico che esisteva prima dei tipi.
    AgentType(name="operator", tools=None),
)

AGENT_TYPES: dict[str, AgentType] = {t.name: t for t in _AGENT_TYPES}
AGENT_TYPE_NAMES: tuple[str, ...] = tuple(AGENT_TYPES)


def get_agent_type(name: str | None) -> AgentType:
    """Risolve un tipo per nome. Solleva :class:`UnknownAgentTypeError`.

    Usata sui percorsi in cui il tipo arriva da fuori (tool ``spawn``, cron,
    codice): un nome sbagliato deve fallire subito e in modo leggibile.
    """
    key = name or DEFAULT_AGENT_TYPE
    try:
        return AGENT_TYPES[key]
    except KeyError:
        raise UnknownAgentTypeError(name) from None


def validate_agent_type(name: str | None) -> str:
    """Ritorna il nome del tipo se valido, altrimenti solleva."""
    return get_agent_type(name).name


def coerce_agent_type(name: object) -> str:
    """Come :func:`validate_agent_type` ma degrada su ``operator``.

    Solo per i record riletti da disco: un record scritto da una versione che
    aveva un tipo poi rimosso non deve rendere impossibile il rilancio del
    lavoro — meglio un operator generico che un lineage irrilanciabile.
    """
    if isinstance(name, str) and name in AGENT_TYPES:
        return name
    logger.warning(
        "Unknown agent_type {!r} in persisted subagent record; degrading to {}",
        name, DEFAULT_AGENT_TYPE,
    )
    return DEFAULT_AGENT_TYPE
