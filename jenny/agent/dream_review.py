"""Review pass di Dream — il run che non aggiunge niente e può soltanto ridurre.

Dream gira ogni due ore e vede *una* voce di storia per run: la domanda che gli
viene posta è sempre «questo fatto nuovo dove lo instrado?», e la risposta è
quasi sempre «da qualche parte». La domanda «questo file è diventato troppo
grande?» non gliela pone nessuno, mai — ed è il motivo per cui le regole di
potatura che il suo prompt già contiene restano teoriche mentre MEMORY.md cresce
in modo monotono.

Questo modulo è il secondo tipo di run: nessuna storia nel prompt, nessun fatto
da instradare, un solo mestiere — far rientrare i file nel budget. La forma è
quella di :mod:`jenny.agent.atlas`: tutta la logica qui, al dispatcher cron
restano l'instradamento e il log.

Tre cose che questo run **non** fa, ognuna con il suo test in
``tests/agent/test_dream_review.py``:

1. **Non tocca ``.dream_cursor``**, né in lettura né in scrittura. Non processa
   storia, quindi non ha nessun cursore da avanzare. Toccarlo qui perderebbe
   per sempre le voci che il run incrementale non ha ancora consolidato, ed è
   esattamente il livelock che il review pass esiste per rompere: Dream che
   rimacina lo stesso batch perché le sue scritture sforano il budget.
2. **Non consulta ``dream_should_advance_cursor`` /
   ``internal_run_should_commit``** per decidere il proprio esito. Quelle
   regole rispondono a «posso dichiarare digerito questo input?», domanda che
   qui non ha un input a cui riferirsi: un review pass che non scrive niente è
   un esito valido, non un progresso da trattenere. L'esito lo dicono i byte su
   disco, prima e dopo. Di quel gruppo di helper serve solo
   ``internal_run_completed``, per distinguere un run finito male.
3. **Non fa lo snapshot.** Lo fa il chiamante, l'unico a sapere se gli snapshot
   sono attivi; qui arriva solo il flag ``snapshotted`` per dire la verità al
   prompt. Il template ha due rami e quello "reversibile" è attaccato alla
   frase il cui scopo è far cancellare di più: passarlo a vanvera significa
   promettere al modello un checkpoint che non esiste.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from jenny.agent.memory import MemoryStore
from jenny.agent.memory_budget import count_chars, render_gauge
from jenny.agent.token_usage import record_response_token_usage
from jenny.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from jenny.agent.memory_budget import FileBudget, WriteSizeGuard

# Valori di ``ReviewOutcome.status``. Stringhe come in ``AtlasOutcome`` e non
# enum: finiscono nei log e nelle asserzioni dei test, e un enum qui
# obbligherebbe ogni chiamante a importare questo modulo solo per confrontare.
STATUS_COMPLETED = "completed"
STATUS_NO_CHANGE = "no-change"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """Esito di un review pass: cosa è successo, e di quanto.

    ``before``/``after`` sono mappe ``label -> caratteri`` sugli stessi file del
    report ricevuto, misurate rispettivamente prima e dopo il run. Viaggiano
    anche quando lo status è ``failed``: un run interrotto a metà può aver già
    scritto, e il chiamante che deve decidere se riprovare ha bisogno di sapere
    se qualcosa si è mosso.
    """

    status: str
    before: dict[str, int]
    after: dict[str, int]

    @property
    def freed(self) -> int:
        """Caratteri liberati in totale; negativo se i file sono cresciuti."""
        return sum(chars - self.after.get(label, 0) for label, chars in self.before.items())


def review_session_key() -> str:
    """Session key di un review pass, es. ``dream:review-20260816-100000``.

    Il prefisso ``dream:`` non è cosmesi ed è la ragione per cui la chiave è
    coniata così e non come ``review:...``: ``is_internal_session_key`` lo copre
    già (quindi la sessione non compare negli elenchi user-facing) e
    ``MemoryStore.prune_dream_sessions`` globba ``dream_*.jsonl`` (quindi il file
    viene ripulito dal meccanismo esistente). Con un prefisso tutto suo ogni
    review pass lascerebbe dietro un file di sessione per sempre, e servirebbe un
    secondo pruner che nessuno si ricorderebbe di scrivere.
    """
    return f"dream:review-{datetime.now():%Y%m%d-%H%M%S}"


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    """``on_progress`` no-op: un run interno non ha nessuno a cui riferire."""


def _timezone_of(agent: Any) -> str | None:
    """Fuso dell'agente, per datare la riga di consumo token. Come in ``atlas``."""
    context = getattr(agent, "context", None)
    return getattr(context, "timezone", None)


def _measure(report: Sequence[FileBudget]) -> dict[str, int]:
    """Rimisura i file del report, ``label -> caratteri``.

    Riusa ``count_chars`` invece di rileggere a modo suo: è la stessa funzione
    con cui ``budget_report`` ha prodotto le misure di partenza (stesso
    ``errors="ignore"``, stesso 0 per file assente), e due implementazioni
    diverse ai due capi del confronto produrrebbero delta inventati sul primo
    byte malformato.
    """
    return {item.label: count_chars(item.path) for item in report}


async def run_dream_review(
    agent: Any,
    *,
    store: MemoryStore,
    report: Sequence[FileBudget],
    snapshotted: bool,
    write_size_guard: WriteSizeGuard | None = None,
) -> ReviewOutcome:
    """Esegue un review pass sui file di memoria e restituisce l'esito.

    *report* è il report di budget già costruito dal chiamante: le misure di
    partenza si prendono di lì e non si rimisurano, così il gauge nel prompt e
    il ``before`` dell'outcome sono lo stesso numero — se divergessero, il
    modello e il log racconterebbero due storie diverse dello stesso run.

    *snapshotted* dice soltanto la verità al prompt: lo snapshot lo fa il
    chiamante (v. il punto 3 del docstring di modulo). *write_size_guard* è il
    gancio che impone il budget sulle scritture ed è opzionale come altrove:
    senza, il run misura e pota ma nessuna scrittura viene mai rifiutata.
    """
    before = {item.label: item.chars for item in report}
    prompt = render_template(
        "agent/dream_review.md",
        # ``for_review=True`` non è un dettaglio: la variante incrementale del
        # gauge dice "consolida prima di aggiungere", e in un run dove per
        # definizione non si aggiunge niente quella riga descrive un'azione che
        # non esiste.
        budget_gauge=render_gauge(report, for_review=True),
        snapshotted=snapshotted,
    )
    # Stesso tool set di Dream: il prompt rimanda a ``agent/dream.md`` per i
    # criteri di cancellazione invece di ricopiarli, e quel rimando sta in piedi
    # solo perché ``ReadFileTool`` qui dentro è montato sull'intero workspace.
    tools = store.build_dream_tools(write_size_guard=write_size_guard)

    resp = None
    try:
        resp = await agent.process_direct(
            prompt,
            session_key=review_session_key(),
            ephemeral=True,
            tools=tools,
            on_progress=_silent,
        )
    except Exception:  # noqa: BLE001 — l'esito viaggia nell'outcome, non in un raise
        # Un review pass è un lavoro di manutenzione: farlo esplodere in faccia
        # al cron porterebbe via anche ciò che viene dopo nello stesso tick,
        # mentre il ``failed`` qui sotto è già tutto quello che il chiamante può
        # farci. Le misure si prendono comunque: il modello può aver scritto
        # prima di morire.
        logger.exception("Dream review pass failed")
        return ReviewOutcome(status=STATUS_FAILED, before=before, after=_measure(report))
    finally:
        # La contabilità dei token sta qui e non nel chiamante, come in
        # ``run_atlas``: questa funzione è l'unico punto che vede la risposta del
        # provider — restituisce un ``ReviewOutcome`` e non rilancia, quindi da
        # fuori il ``resp`` non è raggiungibile. Senza questa riga un turno LLM
        # completo, su un telefono, dentro una feature nata per contenere i
        # costi, non comparirebbe in nessun conteggio.
        #
        # ``source="dream"`` e non ``"dream_review"``: ``_SOURCE_KEYS``
        # (``agent/token_usage.py``) è un elenco chiuso e ``_clean_source``
        # riscrive in silenzio qualunque valore fuori lista in ``"system"``, che
        # non separerebbe i due run — li seppellirebbe nel secchio generico.
        # Separarli davvero vuol dire aggiungere la chiave lì e la sua etichetta
        # nella WebUI; finché non si fa, i due run di Dream restano un aggregato.
        record_response_token_usage(
            resp, source="dream", timezone_name=_timezone_of(agent),
        )

    after = _measure(report)

    # ``failed`` ha la precedenza su qualsiasi riduzione osservata: lo status
    # descrive la salute del run, non il suo effetto collaterale. Un turno
    # interrotto che per caso ha accorciato un file resta un turno interrotto —
    # di quanto abbia ridotto lo dicono ``before``/``after``, che ci sono
    # comunque.
    if not MemoryStore.internal_run_completed(resp):
        logger.warning("Dream review: run did not complete cleanly")
        return ReviewOutcome(status=STATUS_FAILED, before=before, after=after)

    if any(after[label] < chars for label, chars in before.items()):
        outcome = ReviewOutcome(status=STATUS_COMPLETED, before=before, after=after)
        logger.info(
            "Dream review: freed {:,} chars ({})",
            outcome.freed,
            ", ".join(f"{label} {before[label]:,}->{after[label]:,}" for label in before),
        )
        return outcome

    # Nessun file è diminuito. Non è un fallimento e il codice deve dire la
    # stessa cosa che il prompt dice al modello ("a review run that changes
    # nothing is a valid outcome"): un run che non manifattura un edit pur di
    # giustificarsi ha fatto la cosa giusta.
    #
    # In questo ramo cade anche il caso raro in cui qualcosa è *cresciuto* senza
    # che nulla calasse — una ristrutturazione che sposta testo fra i tre file
    # misurati. Non ha un nome suo nel contratto a tre stati, e "no-change" è la
    # collocazione meno fuorviante: nulla è stato liberato. Il delta vero resta
    # leggibile in ``before``/``after``.
    logger.info("Dream review: nothing to shrink")
    return ReviewOutcome(status=STATUS_NO_CHANGE, before=before, after=after)
