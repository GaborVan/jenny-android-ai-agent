"""``journal_append`` — la cattura di un progetto, e l'unica scrittura che
l'agente principale sa fare da sé.

Passo **T2.5** di ``roadmap/taccuino-passi.md``, e nasce da una misura, non da un
disegno. Il 22/08 la politica di cattura ha funzionato al primo colpo — fatto
stabile detto, riga nel diario, stesso turno — ma **passando da uno spawn**:
``orchestrator_mode`` è acceso di default e toglie all'agente principale
``python_exec``, la scrittura e le patch, quindi l'unica strada per appendere una
riga era delegare a un subagent. Una corsa di subagent intera per una riga di
testo, a ogni turno che contiene un fatto: l'opposto esatto della premessa su cui
poggia il passo («costa poco per costruzione, o non succede»).

Da cui un tool **stretto**, nello scope dell'orchestratore. Stretto è la
caratteristica, non un limite:

- **Un solo file possibile**, e non è un argomento: la pagina di oggi del diario
  del progetto del turno. Niente path da sbagliare, niente traversal da validare,
  niente modo di scrivere altrove.
- **Solo in coda.** Non c'è modo di riscrivere una riga esistente, ed è il
  contratto append-only del diario reso vero dal codice invece che promesso da un
  prompt. Il giardiniere (T4) legge quel file assumendolo immobile.
- **Solo dentro un progetto.** Fuori, la cattura non ha una cartella dove
  andare, e il rifiuto lo dice invece di inventarne una — la lezione del passo 6.
- **Niente in sola lettura**, come ogni altra scrittura (passo 4).

Non sostituisce la scrittura vera: creare pagine, riordinare, curare la mappa
restano lavoro da subagent, e devono restarlo — sono le decisioni che il passo
T4 vuole prese a mente fredda. Questo tool fa una cosa sola, ed è la cosa che
capita a ogni turno.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.schema import StringSchema, tool_parameters_schema
from jenny.security.workspace_access import (
    READONLY_TOOL_REFUSAL,
    current_turn_is_readonly,
    current_workspace_scope,
)
from jenny.utils.wiki_paths import is_wiki_root, journal_page_name, wiki_journal_dir

# Non c'e' un progetto: il rifiuto dice **dove** vive il diario invece di dire
# solo che qui non c'e'. E' la parte che Jenny ridice all'utente, e la
# differenza fra "aprine uno" e un "non posso" che manda via a mani vuote.
_NO_PROJECT_REFUSAL = (
    "No journal here: the working journal belongs to a project, and this conversation is not "
    "one. Nothing was written. If this is worth keeping, it belongs either in the personal "
    "memory (say so and it goes to MEMORY.md) or in a project — the chip above the composer "
    "opens one. Do not look for another file to append to."
)

# Il tetto di una riga. Una riga di diario e' un fatto, non un paragrafo: quel
# che non ci sta e' materiale da pagina, e il posto di una pagina non lo decide
# la cattura (v. la docstring del modulo).
_MAX_TEXT_CHARS = 500

_PARAMETERS = tool_parameters_schema(
    text=StringSchema(
        "The fact to record, as one short line and in the user's own terms — what will still "
        "be true next week, not what was said. No leading '- ', no timestamp: both are added. "
        f"At most {_MAX_TEXT_CHARS} characters; anything longer belongs on a page, not here."
    ),
    required=["text"],
)


class JournalAppendTool(Tool):
    """Append one line to the current project's journal page for today."""

    # Nello scope dell'orchestratore **di proposito**: e' l'unica scrittura che
    # l'agente principale deve poter fare senza delegare, e la ragione per cui
    # esiste. Anche nel core e nel subagent, cosi' un turno con i tool pieni non
    # si ritrova a fare a mano quel che qui e' una chiamata.
    _scopes = {"core", "orchestrator", "subagent"}

    def __init__(
        self,
        today: Any = None,
        root: Path | None = None,
        origin_marker: str = "",
    ) -> None:
        # Iniettabile per i test; in produzione nessuno lo passa.
        self._today = today or date.today
        # **Il progetto iniettato invece di dedotto.** Su un turno dell'utente il
        # progetto si ricava dallo scope legato, ed e' giusto: la cartella non
        # deve poter divergere dalla conversazione. Ma una **passata interna**
        # gira con lo scope di default (l'installazione), quindi la deduzione
        # darebbe "nessun progetto" e il tool rifiuterebbe. Chi costruisce la
        # cassetta di una passata sa su quale progetto sta lavorando, e lo passa.
        self._root = root
        # Marcatore d'origine, per una riga che non arriva dalla conversazione ma
        # da un recupero a posteriori. Sta nel **codice** e non nel prompt perche'
        # e' l'unico modo che ha di non essere dimenticato, e una riga di diario
        # senza origine e' indistinguibile da una detta a voce quel giorno.
        self._origin_marker = origin_marker

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return True

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls()

    @property
    def name(self) -> str:
        return "journal_append"

    @property
    def description(self) -> str:
        return (
            "Append one line to this project's working journal (raw/journal/<today>.md). "
            "Use it the moment the user says something that will still be true next week — a "
            "constraint, a decision, a preference, a name, a date — before answering them. "
            "Append-only by construction: it can neither rewrite a line nor touch any other "
            "file, so it is always safe to call. Only inside a project."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return _PARAMETERS

    @tool_parameters(_PARAMETERS)
    async def execute(self, text: str = "", **_: Any) -> str:
        line = " ".join((text or "").split())
        if line and self._origin_marker:
            line = f"{self._origin_marker} {line}"
        if not line:
            return "Nothing to append: `text` was empty."
        if len(line) > _MAX_TEXT_CHARS:
            return (
                f"Too long for a journal line ({len(line)} characters, max {_MAX_TEXT_CHARS}). "
                "A journal line is one fact; this is page material."
            )

        # L'ordine dei due gate non e' indifferente. Prima "non c'e' un
        # progetto", che e' vero indipendentemente dal modo del turno: dire
        # "sola lettura" a chi non ha nemmeno una cartella dove scrivere lo
        # mette a cercare l'interruttore per un problema che l'interruttore non
        # risolve.
        scope = current_workspace_scope()
        root = self._root or (scope.project_path if scope is not None else None)
        if root is None or not is_wiki_root(root):
            return _NO_PROJECT_REFUSAL
        if current_turn_is_readonly():
            return READONLY_TOOL_REFUSAL

        return await self._append(root, line)

    async def _append(self, root: Path, line: str) -> str:
        day = self._today()
        page = wiki_journal_dir(root) / journal_page_name(day)
        entry = f"- {datetime.now().strftime('%H:%M')} — {line}\n"
        try:
            page.parent.mkdir(parents=True, exist_ok=True)
            fresh = not page.exists()
            # Append in `a`, e non "leggi-modifica-riscrivi": il contratto
            # append-only vale anche contro se stessi, e una riscrittura potrebbe
            # perdere quel che un altro scrittore ha appeso nel frattempo.
            with page.open("a", encoding="utf-8") as fh:
                if fresh:
                    fh.write(f"# {day.isoformat()}\n\n")
                fh.write(entry)
        except OSError as exc:
            logger.warning("journal_append su {} fallito: {}", page, exc)
            return f"Could not append to the journal: {exc}"

        logger.info("journal_append: {} <- {}", page.name, line[:60])
        return f"Appended to {page.parent.name}/{page.name}: {entry.strip()}"


TOOLS = [JournalAppendTool]
