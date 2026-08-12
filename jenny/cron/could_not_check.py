"""Il marcatore con cui un turno silenzioso dichiara "non ho potuto controllare".

Perché il testo finale del turno e non un tool nuovo: su un controllo periodico
quel testo esiste già, è già scartato (il turno è SILENT per costruzione) e oggi
non significa niente. Dargli un significato non aggiunge superficie che il
modello debba imparare, non può raggiungere l'utente per sbaglio, e non costa
una chiamata LLM in più. Un tool dedicato sarebbe invece comparso nell'elenco di
OGNI turno, chat comprese: il registry di un turno cron è quello di default.

Da non confondere col sentinella rifiutato in ``AgentLoop._process_message``: là
si decideva **se consegnare**, cioè un atto con un effetto sull'utente, e per
quello il tool ``message`` è e resta l'unica strada. Qui il modello non consegna
niente, dichiara un fatto su di sé.

Il modulo sta a parte — e non dentro ``bound_runner`` dove è nato con B8 —
perché ha due lettori: il monitor cron (un controllo per turno, marcatore senza
riferimento) e l'heartbeat (N task in un turno solo, marcatore con il numero del
task). Un parser solo, due chiamanti: la forma che il modello scrive resta una.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# La parola che apre la riga. Maiuscola e senza spazi: è un token, non prosa.
COULD_NOT_CHECK_MARKER = "CHECK_FAILED"

# Il secondo marcatore, e il motivo per cui ce n'è un secondo: l'agente
# principale gira in ``orchestrator_mode`` e non ha ``python_exec``, quindi un
# task che deve *fare* qualcosa viene delegato con ``spawn`` — che ritorna
# subito. Il turno dell'heartbeat finisce prima che il subagent abbia iniziato,
# e la regola "nessun marcatore = il task è stato eseguito" leggerebbe quel
# silenzio come un successo. Con questa riga il turno dichiara invece "l'esito
# non lo so ancora", la sequenza di guasti del task non viene azzerata, e a
# scriverne il verdetto è il turno che riceve il risultato
# (:mod:`jenny.cron.heartbeat_followup`).
DELEGATED_MARKER = "CHECK_DELEGATED"

# Quante esecuzioni consecutive senza controllo prima di avvisare l'utente.
# Tre, a mezz'ora di intervallo, fanno circa un'ora e mezza: abbastanza da non
# reagire a una rete che va e viene, poco abbastanza da non lasciare un
# controllo morto per una giornata. Il silenzio di un controllo sano resta
# gratis — qui non si muove niente finché il modello non dichiara di non aver
# potuto controllare.
ESCALATE_AFTER_FAILURES = 3

# Il motivo finisce in ``CronJobState.last_error`` e nella run record: va tenuto
# corto, non è un log.
REASON_MAX_CHARS = 200

# Rumore markdown che un modello mette volentieri attorno a una riga
# "importante" — punto elenco compreso: un modello che scrive N marcatori in
# fondo alla risposta li mette volentieri in elenco.
_MARKER_DECORATION = "*_`#>-+ \t"

# Separatori ammessi fra il marcatore (o il riferimento) e il motivo.
_SEPARATORS = ": -–—"

# ``CHECK_FAILED 2: motivo`` — e le varianti che un modello scrive senza
# pensarci: ``#2``, ``[2]``, ``2)``, ``2.``, o il solo numero senza motivo.
_REF_RE = re.compile(r"^\[?\s*#?(\d{1,3})\s*[\])\.]?\s*[:\.\-–—]?\s*(.*)$")


@dataclass(frozen=True)
class CouldNotCheckMark:
    """Una riga di marcatore, letta.

    Attributes:
        ref: il riferimento al task, quando c'è (l'heartbeat numera i suoi
            task nel prompt). ``None`` su un monitor, che ha un controllo solo
            e quindi non ha niente da distinguere.
        reason: riga breve scritta dal modello su cosa lo ha bloccato. Può
            essere vuota: il marcatore senza motivo resta un marcatore.
    """

    ref: str | None
    reason: str


def parse_could_not_check_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge TUTTE le righe di marcatore nel testo finale di un turno.

    Una lista e non un singolo esito perché un turno di heartbeat copre N task
    e può fallirne più d'uno: il monitor, che ne ha uno solo, guarda la prima e
    ignora il resto.
    """
    return _parse_marks(final_text, COULD_NOT_CHECK_MARKER)


def parse_delegated_marks(final_text: str | None) -> list[CouldNotCheckMark]:
    """Legge le righe ``CHECK_DELEGATED``: task il cui esito non si sa ancora.

    Stessa forma, stesso parser: la sintassi che il modello deve ricordare
    resta una sola, cambia solo la parola iniziale. ``reason`` qui è ciò che è
    stato delegato, e serve solo al log — a contare è quale task.
    """
    return _parse_marks(final_text, DELEGATED_MARKER)


def _parse_marks(final_text: str | None, marker: str) -> list[CouldNotCheckMark]:
    """Corpo condiviso dai due marcatori. Nessuno dei due è prefisso dell'altro."""
    marks: list[CouldNotCheckMark] = []
    for line in (final_text or "").splitlines():
        stripped = line.strip().lstrip(_MARKER_DECORATION)
        if not stripped.startswith(marker):
            continue
        rest = stripped[len(marker):].rstrip(_MARKER_DECORATION)
        head = rest.lstrip()
        if head[:1] and head[0] in _SEPARATORS:
            # Forma B8: ``CHECK_FAILED: motivo``. Il motivo può contenere due
            # punti a sua volta, quindi qui non si va a cercare un separatore —
            # si toglie quello iniziale e basta.
            marks.append(
                CouldNotCheckMark(None, rest.lstrip(_SEPARATORS).strip()[:REASON_MAX_CHARS])
            )
            continue
        match = _REF_RE.match(head)
        if match is not None:
            marks.append(
                CouldNotCheckMark(match.group(1), match.group(2).strip()[:REASON_MAX_CHARS])
            )
            continue
        marks.append(CouldNotCheckMark(None, head.strip()[:REASON_MAX_CHARS]))
    return marks


def could_not_check_reason(final_text: str | None) -> str | None:
    """Legge il marcatore nel testo finale di un monitor.

    Returns:
        ``None`` se il marcatore non c'è, cioè il controllo è avvenuto.
        Altrimenti il motivo dichiarato dal modello, che può essere la stringa
        vuota se non ne ha scritto uno: chi chiama deve distinguere con
        ``is not None``, non sulla verità della stringa.
    """
    marks = parse_could_not_check_marks(final_text)
    return marks[0].reason if marks else None
