"""Il giardiniere — la passata che trasforma il diario di un progetto in pagine.

Passo **T4.2** di ``roadmap/taccuino-passi.md``. La cattura (T2) scrive righe di
diario mentre si conversa; qui quelle righe diventano pagine e la mappa torna
vera. Due mestieri separati di proposito: la cattura deve costare una chiamata e
non decidere niente, il giardiniere decide (nomi, struttura, cosa merita una
pagina) e per farlo ha bisogno di essere solo, a sessione ferma.

**È il gemello di Atlas**, e la somiglianza è deliberata fino ai nomi dei metodi:
inventario deterministico costruito in Python e messo nel prompt (al modello resta
il giudizio, non l'esplorazione), superficie di scrittura chiusa da un
``ToolRegistry`` costruito a mano, un solo runner condiviso fra il comando manuale
e — in T4.3 — il job cron, e il predicato di commit di Dream per decidere se il
cursore può avanzare.

Tre cose che questo modulo sa e che vale scrivere:

1. **Il confinamento è il registry, non lo scope.** Un turno interno gira su
   ``INTERNAL_CHANNEL``, e ``WorkspaceScopeResolver.for_turn`` per ogni canale che
   non sia la WebUI restituisce lo scope **di default** — l'intera installazione
   scrivibile. Quel che tiene il giardiniere dentro ``wiki/`` è la cassetta dei
   tool che gli si passa, e nient'altro. Da cui la regola: **una porta nella
   cassetta è una via d'uscita**, e ``spawn_subagent``/``python_exec``/``message``
   non ci entrano.
2. **I percorsi sono relativi al workspace**, non al progetto — per la stessa
   ragione per cui Atlas mette ``memory/WIKI.md`` e non un assoluto. La base dei
   percorsi relativi è ``project_path`` dello scope legato, che per un turno
   interno è la radice dell'installazione; e su Android un assoluto viene
   rifiutato comunque, perché la dir dati è raggiungibile sotto due nomi
   (``/data/user/0`` e ``/data/data``) e la allowlist ne conosce uno.
3. **Il log lo scrive il codice, non il modello.** Toglie una destinazione dalla
   superficie di scrittura (che resta ``wiki/`` e basta) e rende la riga di log
   affidabile: un modello che scrive il proprio registro può raccontare una
   passata che non ha fatto. Il prezzo è che la riga dice quel che il codice sa —
   quante righe ha digerito, quante scritture sono riuscite — e non i nomi delle
   pagine.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from jenny.agent.gardener_state import (
    MAX_DELTA_LINES,
    JournalDelta,
    read_journal_delta,
    read_state,
    write_state,
)
from jenny.session.keys import GARDENER_SESSION_PREFIX
from jenny.utils.prompt_templates import render_template
from jenny.utils.wiki_paths import is_wiki_root, iter_wiki_pages

# Tetto sull'elenco delle pagine messo nel prompt. Serve al giardiniere per non
# creare un doppione di una pagina che esiste, quindi è materiale di lavoro e non
# decorazione — ma oltre trecento voci il prompt costa più del servizio che rende,
# e il taglio si dice invece di tacerlo.
_MAX_INVENTORY_ENTRIES = 300

# Tetti sui due documenti che entrano interi. Generosi: si pagano una volta per
# passata, non una volta per turno come la mappa del passo T3.
_MAX_MAP_CHARS = 8000
_MAX_AGENTS_CHARS = 4000

# Il marcatore con cui la passata chiude. **Un marcatore e non prosa**:
# interpretare testo libero e' il modo in cui questo genere di cose smette di
# funzionare senza che nessuno se ne accorga — un giorno il modello scrive la
# stessa cosa con altre parole e il canale e' morto in silenzio.
_FLAG_MARKER = "FLAG:"
_NO_FLAG_MARKER = "NOTHING TO FLAG"

# Tetto della riga di segnalazione nel log. Il log e' "una riga per operazione":
# un paragrafo qui lo rende illeggibile, ed e' l'unico registro che c'e'.
_MAX_FLAG_CHARS = 300

# Quanti messaggi **dell'utente** entrano nel controllo incrociato, e il tetto in
# caratteri che li contiene comunque. Non c'e' un cursore sul transcript, e la
# scelta e' deliberata: le righe del transcript non portano un timestamp e il file
# attivo **ruota** in segmenti, quindi un conteggio di righe si azzererebbe senza
# dirlo. Le ultime N invece sono sempre leggibili, e lo stato del confronto e' il
# **diario stesso**: quel che e' stato recuperato ci sta dentro, quindi il giro
# dopo non si recupera due volte. Idempotente per costruzione, senza stato nuovo.
_RECENT_USER_MESSAGES = 40
_MAX_TRANSCRIPT_CHARS = 6000

# Il marcatore di una riga di diario nata da un recupero e non dalla
# conversazione. Sta nel codice (v. ``JournalAppendTool.origin_marker``).
RECOVERED_MARKER = "[recovered]"


@dataclass(frozen=True)
class GardenerOutcome:
    """L'esito di una passata, nella forma che i chiamanti sanno tradurre."""

    status: str
    elapsed: float = 0.0
    lines: int = 0
    writes: int = 0
    detail: str = ""

    @property
    def ran(self) -> bool:
        """Se una chiamata al provider è avvenuta."""
        return self.status not in ("skipped_no_delta", "skipped_not_a_project")


class GardenerStore:
    """File I/O, prompt e cassetta dei tool di una passata su **un** progetto."""

    def __init__(
        self,
        root: Path,
        workspace: Path,
        *,
        max_delta_lines: int = MAX_DELTA_LINES,
        today: Any = None,
    ) -> None:
        # **Entrambi risolti, qui e non ai punti d'uso.** Su Android la dir dati
        # e' raggiungibile come ``/data/user/0/<pkg>`` e ``Path.resolve()`` la
        # riscrive in ``/data/data/<pkg>``: se una delle due radici arriva
        # risolta e l'altra no, ``relative_to`` alza ``ValueError`` e il percorso
        # che finisce nel prompt e' sbagliato. Misurato sul telefono il 23/08 —
        # il modello ha scritto quattro pagine perfette in ``zz-t4/wiki/`` invece
        # di ``wikis/zz-t4/wiki/``, e sono state rifiutate tutte.
        self.root = root.resolve(strict=False)
        self.workspace = workspace.resolve(strict=False)
        self.max_delta_lines = max_delta_lines
        # Iniettabile per i test; in produzione nessuno lo passa.
        self._today = today or date.today

    @classmethod
    def for_project(
        cls, workspace: Path, name: str, *, wikis_dir_name: str = "wikis"
    ) -> "GardenerStore | None":
        """Lo store del progetto *name*, o ``None`` se quella cartella non è un progetto.

        ``None`` e non un'eccezione: il chiamante che itera i progetti non deve
        avvolgere ogni giro in un ``try``, e quello che risponde a un comando ha
        un rifiuto da scrivere, non un errore da propagare.
        """
        root = (workspace / wikis_dir_name / name).resolve(strict=False)
        wikis_root = (workspace / wikis_dir_name).resolve(strict=False)
        # Il nome arriva da una chiave di sessione o da un argomento di comando:
        # un ``..`` non deve poter far uscire la passata dalla cartella dei
        # progetti (stessa guardia di ``WorkspaceScopeResolver.for_project``).
        if root == wikis_root or wikis_root not in root.parents:
            logger.warning("gardener: {} cade fuori da {}", name, wikis_root)
            return None
        if not is_wiki_root(root):
            return None
        return cls(root, workspace)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def rel_root(self) -> str:
        """La radice del progetto **relativa al workspace**: ``wikis/viaggio``.

        È la forma in cui i percorsi vanno nel prompt (v. la docstring del
        modulo), e non è una preferenza estetica: un assoluto verrebbe rifiutato
        dalla guardia.
        """
        try:
            return self.root.relative_to(self.workspace).as_posix()
        except ValueError:
            # Non deve capitare — le due radici sono risolte alla costruzione, e
            # un progetto sta sempre dentro il workspace. Se capita e' un difetto
            # di programmazione, e va **detto**: il fallback restituisce un
            # percorso dall'aria giusta su cui ogni scrittura verra' rifiutata in
            # silenzio, che e' esattamente come questo bug si e' presentato la
            # prima volta.
            logger.error(
                "gardener: {} non e' dentro il workspace {}: i percorsi del prompt "
                "saranno rifiutati",
                self.root, self.workspace,
            )
            return self.root.name

    # -- input ---------------------------------------------------------------

    def read_delta(self) -> JournalDelta:
        return read_journal_delta(
            self.root, read_state(self.root), max_lines=self.max_delta_lines
        )

    def commit(self, delta: JournalDelta, *, at: datetime | None = None) -> None:
        """Registra il delta come letto. Da chiamare **solo** a passata riuscita."""
        write_state(self.root, read_state(self.root).advanced(delta, at=at))

    def build_inventory(self) -> str:
        entries = iter_wiki_pages(self.root / "wiki")
        if not entries:
            return "_(no pages yet — this project is starting from the journal)_"
        shown = entries[: _MAX_INVENTORY_ENTRIES]
        lines = [f"- `{rel}` — {title}" for rel, title in shown]
        if len(entries) > len(shown):
            lines.append(
                f"- _(list truncated: {len(entries)} pages in all, {len(shown)} shown)_"
            )
        return "\n".join(lines)

    def _read_capped(self, path: Path, cap: int, label: str) -> str:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        if len(text) <= cap:
            return text
        # Mai troncare zitti: la nota è la differenza fra "questo è tutto" e
        # "questo è quanto ci stava" (la lezione di Atlas e del tetto di T3).
        return text[:cap] + (
            f"\n\n[{label} continues — {len(text)} characters in all; read the file for the rest]"
        )

    @staticmethod
    def _render_delta(delta: JournalDelta) -> str:
        blocks: list[str] = []
        for page in delta.files:
            blocks.append(f"**{page.path}**\n\n" + "\n".join(page.lines))
        body = "\n\n".join(blocks)
        if delta.left_behind:
            body += (
                f"\n\n_({delta.left_behind} further journal lines were left for the next pass: "
                "this pass has a ceiling. Do not try to read them — they will arrive.)_"
            )
        return body

    def read_journal_days(self, delta: JournalDelta) -> str:
        """I file di diario che il delta tocca, **per intero**.

        Serve al controllo incrociato e non alla promozione: il delta contiene
        solo le righe *nuove*, e un fatto già catturato sta spesso **sotto** il
        cursore. Confrontare il detto col solo delta segnalerebbe come mancante
        tutto quello che una passata precedente aveva già letto — cioè, sulla
        seconda passata di una giornata, quasi tutto.
        """
        blocks: list[str] = []
        for page in delta.files:
            text = self._read_capped(self.root / page.path, _MAX_MAP_CHARS, "The journal")
            if text:
                blocks.append(f"**{page.path}**\n\n{text}")
        return "\n\n".join(blocks)

    def build_prompt(self, delta: JournalDelta) -> str:
        """Prompt completo della passata: meccanismo, poi i dati, ognuno recintato.

        I dati sono **recintati a quattro backtick** per la ragione del passo T3:
        una riga di diario o una pagina possono contenere intestazioni ``#``, e
        senza recinto sbucherebbero nella struttura del prompt come se fossero
        sezioni di istruzioni. Quel che sta in un file dell'utente è dato, e va
        nel canale dei dati.
        """
        parts = [
            render_template(
                "agent/gardener.md",
                strip=True,
                project_path=self.rel_root,
                project_name=self.name,
            ),
            "## New journal lines\n\n" + self._render_delta(delta),
            "## The map as it stands (`{}/wiki/index.md`)\n\n````markdown\n{}\n````".format(
                self.rel_root,
                self._read_capped(self.root / "wiki" / "index.md", _MAX_MAP_CHARS, "The map")
                or "_(empty)_",
            ),
            "## Pages that already exist\n\n" + self.build_inventory(),
        ]
        said, said_truncated = read_recent_user_messages(self.name)
        if said:
            # Il lato "detto" e il lato "registrato", **accanto**: il confronto è
            # una lettura, non una ricerca.
            lines = "\n".join(f"- {message}" for message in said)
            if said_truncated:
                lines += "\n- _(older messages not shown: this is the recent tail)_"
            parts.append(
                "## What the user actually said, most recent last\n\n````text\n"
                + lines
                + "\n````"
            )
            recorded = self.read_journal_days(delta)
            if recorded:
                parts.append(
                    "## What the journal recorded on those days, in full\n\n````markdown\n"
                    + recorded
                    + "\n````"
                )
        agents = self._read_capped(
            self.root / "AGENTS.md", _MAX_AGENTS_CHARS, "This project's instructions"
        )
        if agents:
            parts.append(
                "## This project's own instructions (`{}/AGENTS.md`)\n\n````markdown\n{}\n````"
                .format(self.rel_root, agents)
            )
        return "\n\n---\n\n".join(parts)

    # -- sandbox -------------------------------------------------------------

    def build_tools(self):
        """La cassetta della passata: legge dentro il progetto, scrive in ``wiki/``.

        **Questa funzione è il confinamento** (v. la docstring del modulo), quindi
        due proprietà vanno lette qui e non altrove: l'elenco dei tool è chiuso —
        nessuno spawn, nessun ``python_exec``, nessun ``message``, cioè nessuna
        porta verso una scrittura per interposta persona — e la sola directory
        scrivibile è ``wiki/``.

        ``.resolve()`` su entrambe le radici per la stessa ragione di Atlas: su
        Android la dir dati è esposta come ``/data/user/0/<pkg>`` ma ``resolve()``
        la riscrive in ``/data/data/<pkg>``, e se la base di risoluzione e la
        allowlist restano in forme diverse la guardia anti-symlink scatta e la
        passata non riesce a scrivere niente.
        """
        from jenny.agent.tools.apply_patch import ApplyPatchTool
        from jenny.agent.tools.file_state import FileStates
        from jenny.agent.tools.filesystem import (
            EditFileTool,
            ListDirTool,
            ReadFileTool,
            WriteFileTool,
        )
        from jenny.agent.tools.registry import ToolRegistry
        from jenny.agent.tools.search import FindFilesTool, GrepTool

        tools = ToolRegistry()
        file_states = FileStates()
        root = self.root.resolve()
        pages = (root / "wiki").resolve()

        # Lettura: dentro il progetto. Non l'intera installazione come Atlas —
        # il giardiniere non ha niente da leggere in un altro progetto, e il
        # prompt di progetto dice che il lavoro fra progetti non esiste.
        for read_only_tool in (ReadFileTool, ListDirTool, FindFilesTool, GrepTool):
            tools.register(read_only_tool(
                workspace=root,
                allowed_dir=root,
                file_states=file_states,
            ))
        # Scrittura: solo ``wiki/``. Il diario resta fuori (è l'input, ed è
        # append-only), ``AGENTS.md`` resta fuori (le premesse le cambia
        # l'utente), ``raw/`` e ``audit/`` restano fuori. Il log non è qui perché
        # lo scrive il codice.
        for write_tool in (WriteFileTool, EditFileTool, ApplyPatchTool):
            tools.register(write_tool(
                workspace=root,
                allowed_dir=pages,
                file_states=file_states,
                restrict_to_workspace=True,
            ))
        # ``journal_append`` è **l'unica scrittura fuori da ``wiki/``** che si
        # concede, e la ragione è che non può violare la regola che protegge:
        # appende in coda per costruzione, quindi non riscrive la fonte da cui sta
        # promuovendo. Il progetto è iniettato perché una passata interna gira con
        # lo scope di default e la deduzione darebbe "nessun progetto".
        from jenny.agent.tools.journal import JournalAppendTool

        tools.register(JournalAppendTool(root=root, origin_marker=RECOVERED_MARKER))
        tools.file_states = file_states
        return tools

    # -- tracce --------------------------------------------------------------

    def session_key(self) -> str:
        """Chiave della passata, es. ``gardener:viaggio-20260823-213000``."""
        return f"{GARDENER_SESSION_PREFIX}{self.name}-{datetime.now():%Y%m%d-%H%M%S}"

    def log_pass(
        self,
        delta: JournalDelta,
        *,
        elapsed: float,
        writes: int,
        flag: str | None = None,
    ) -> None:
        """Una riga in ``log/AAAAMMGG.md``: il solo posto dove si vede la passata.

        Si scrive **solo se qualcosa è stato scritto**: una passata che non
        promuove niente è il caso normale, e una riga di log per ogni giro a
        vuoto renderebbe illeggibile l'unico registro che c'è.
        """
        day = self._today()
        page = self.root / "log" / f"{day.strftime('%Y%m%d')}.md"
        days = ", ".join(Path(f.path).stem for f in delta.files)
        entry = (
            f"## [{datetime.now():%H:%M}] gardener | {delta.line_count} journal lines "
            f"({days}) → {writes} writes in {elapsed:.1f}s\n"
        )
        if flag:
            entry += f"- flagged: {flag}\n"
        try:
            page.parent.mkdir(parents=True, exist_ok=True)
            fresh = not page.exists()
            with page.open("a", encoding="utf-8") as fh:
                if fresh:
                    fh.write(f"# {day.isoformat()}\n\n")
                fh.write(entry)
        except OSError as exc:
            # Il log è una traccia, non il lavoro: se non si scrive, la passata
            # resta valida e il cursore avanza comunque.
            logger.warning("gardener: log non scritto su {}: {}", page, exc)


async def _silent(*_args: Any, **_kwargs: Any) -> None:
    pass


def read_recent_user_messages(
    name: str,
    *,
    limit: int = _RECENT_USER_MESSAGES,
    max_chars: int = _MAX_TRANSCRIPT_CHARS,
) -> tuple[list[str], bool]:
    """Gli ultimi messaggi **dell'utente** in quel progetto, e se sono stati tagliati.

    Letti dal codice e messi nel prompt, **non** esposti come file: la cassetta
    del giardiniere resta chiusa sulla cartella del progetto, e il transcript sta
    fuori (``.jenny/webui/``). Allargargli la superficie di lettura per questa
    cosa sola sarebbe pagare in permessi quel che si puo' pagare in prompt.

    Il transcript non e' il registro del modello: la compattazione riscrive
    ``sessions/``, non questo file. E' per questo che serve — e' il solo posto
    dove resta quel che l'utente ha detto davvero.
    """
    from jenny.session.keys import WEBUI_CHANNEL, project_session_key
    from jenny.webui.transcript_store import webui_transcript_path

    try:
        path = webui_transcript_path(f"{WEBUI_CHANNEL}:{project_session_key(name)}")
    except Exception:  # noqa: BLE001 — senza transcript il controllo salta, non rompe
        return [], False
    if not path.is_file():
        return [], False

    said: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw or "user" not in raw:
                    # Filtro grezzo prima di parsare: un transcript e' fatto in
                    # gran parte di delta, e parsarli tutti per buttarli costa.
                    #
                    # **Soprainsieme, e non quasi-esatto.** Prima cercava
                    # ``'"user"'`` con le virgolette, che e' quasi la condizione
                    # finale — e siccome ``json.dumps`` scappa le virgolette nei
                    # testi, nessuna riga di ragionamento realistica poteva
                    # passare qui. Risultato: il controllo vero sotto era
                    # irraggiungibile, quindi **non provabile** (tre mutazioni di
                    # fila sopravvissute prima di capirlo). Un filtro grezzo deve
                    # ammettere piu' del necessario e lasciar decidere il
                    # controllo vero; il prezzo e' parsare i delta che nominano
                    # l'utente, che e' una minoranza.
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if row.get("event") != "user" and row.get("role") != "user":
                    continue
                text = row.get("text") or row.get("content")
                if isinstance(text, str) and text.strip():
                    said.append(" ".join(text.split()))
    except OSError as exc:
        logger.warning("gardener: transcript di {} illeggibile: {}", name, exc)
        return [], False

    truncated = len(said) > limit
    said = said[-limit:]
    # Il tetto in caratteri toglie **dalla testa**: i messaggi piu' recenti sono
    # quelli che la cattura puo' aver mancato adesso.
    total = 0
    kept: list[str] = []
    for message in reversed(said):
        total += len(message)
        if total > max_chars and kept:
            truncated = True
            break
        kept.append(message)
    return list(reversed(kept)), truncated


def extract_flag(reply: Any) -> str | None:
    """La riga di segnalazione con cui la passata ha chiuso, o ``None``.

    Il canale nasce da un buco: la risposta della passata serviva al predicato di
    commit e alla contabilita' token, e il **testo** veniva buttato — quindi il
    prompt diceva «se due pagine si contraddicono, dillo» e quel report non
    arrivava a nessuno.

    Adesso la contraddizione ha due destinazioni, per due pubblici diversi: la
    sezione aperta della **mappa**, che il modello scrive da se' e che entra nel
    prompt di ogni turno (quindi raggiunge la conversazione), e una riga nel
    **log**, che e' la storia che una persona rilegge. Qui si estrae la seconda.

    Si cerca il marcatore e nient'altro. Un testo senza marcatore non e' un
    errore — una passata che ha scritto le pagine giuste e si e' scordata la
    formula ha fatto il lavoro — e vale "niente da segnalare".
    """
    text = getattr(reply, "content", None)
    if not isinstance(text, str):
        return None
    # Dal fondo: il marcatore chiude la risposta, e cercandolo dall'inizio si
    # prenderebbe la riga in cui il modello *cita* il contratto ragionando.
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith(_NO_FLAG_MARKER):
            return None
        if line.startswith(_FLAG_MARKER):
            flag = line[len(_FLAG_MARKER):].strip()
            return flag[:_MAX_FLAG_CHARS] or None
    return None


async def _checkpoint(agent: Any) -> None:
    """Checkpoint del workspace prima che la passata scriva. **Fail-open.**

    Il giardiniere è il primo lavoro periodico che scrive dentro le cartelle
    *dell'utente* e non in un file derivato: Atlas ricostruisce ``memory/WIKI.md``
    al run dopo, una pagina scritta a mano che venisse sovrascritta non si
    ricostruisce da niente — il diario copre solo quel che dal diario è nato.

    **Fail-open, e non è pigrizia:** il verso opposto («nessuna passata senza
    checkpoint») trasformerebbe uno store di snapshot pieno in un taccuino che
    smette di lavorare in silenzio, che è un guasto peggiore di quello che
    previene. Il checkpoint è una rete, non un permesso.

    E **al modello non si dice niente.** Dream ha un ramo di prompt che promette
    la reversibilità, e serve a fargli potare di più; qui non c'è e non ci va:
    aggiungere-e-promuovere è la regola *anche* con la rete, e prometterla
    sposterebbe il giudizio nella direzione sbagliata.
    """
    hook = getattr(agent, "take_snapshot", None)
    if not callable(hook):
        # Fuori dal gateway (test, ispezione) la rete non c'è. Si dice a DEBUG e
        # si prosegue: che in produzione ci sia lo garantisce il container, e un
        # test sul cablaggio.
        logger.debug("gardener: nessun gancio di snapshot, passata senza rete")
        return
    try:
        await hook("pre_gardener")
    except Exception:
        logger.exception("gardener: snapshot pre-passata fallito; si prosegue")


async def run_gardener(agent: Any, store: GardenerStore) -> GardenerOutcome:
    """Esegue una passata su un progetto e restituisce l'esito.

    Unico punto di ingresso, come ``run_atlas``: lo useranno lo slash command
    ``/gardener`` e il job cron di T4.3. Non c'è un ``force``: i tre orologi
    dell'innesco (delta, fermo, distanza) stanno nel chiamante, e qui resta la
    sola condizione che è del lavoro e non della politica — **se non c'è niente da
    leggere non si parte**, perché una passata su un delta vuoto è un turno LLM
    con un prompt senza materiale.
    """
    from jenny.agent.memory import MemoryStore
    from jenny.agent.token_usage import record_response_token_usage

    delta = store.read_delta()
    if delta.is_empty:
        logger.debug("gardener: niente da leggere in {}", store.name)
        return GardenerOutcome(status="skipped_no_delta")

    # Dopo il cancello del delta e prima di qualunque scrittura: una passata che
    # non parte non ha niente da proteggere, e uno snapshot per tick a vuoto
    # sarebbe una scansione del workspace ogni mezz'ora per niente.
    await _checkpoint(agent)

    t0 = time.monotonic()
    resp = None
    tools = store.build_tools()
    try:
        resp = await agent.process_direct(
            store.build_prompt(delta),
            session_key=store.session_key(),
            ephemeral=True,
            tools=tools,
            on_progress=_silent,
        )
    except Exception as exc:  # noqa: BLE001 — l'esito viaggia nell'outcome
        logger.exception("gardener: passata su {} fallita", store.name)
        return GardenerOutcome(
            status="failed",
            elapsed=time.monotonic() - t0,
            lines=delta.line_count,
            detail=str(exc),
        )
    finally:
        record_response_token_usage(
            resp, source="gardener", timezone_name=_timezone_of(agent)
        )

    elapsed = time.monotonic() - t0
    file_states = getattr(tools, "file_states", None)
    writes = int(getattr(file_states, "writes_ok", 0) or 0)

    if MemoryStore.internal_run_should_commit(resp, file_states):
        # Il cursore avanza anche a zero scritture: «niente da promuovere» è un
        # esito, e riproporre le stesse righe al giro dopo darebbe la stessa
        # risposta a un costo nuovo.
        store.commit(delta)
        flag = extract_flag(resp)
        if flag:
            # A WARNING: e' la sola cosa che una passata puo' dire e che vale la
            # pena vedere passando dai log, senza aprire il file.
            logger.warning("gardener: {} segnala — {}", store.name, flag)
        # Il log si scrive se qualcosa e' stato scritto **oppure** se c'e' una
        # segnalazione: una passata a vuoto non lascia traccia (era la regola, e
        # resta), ma una segnalazione e' la cosa piu' importante che una passata
        # possa dire e non si perde per non aver promosso niente.
        if writes or flag:
            store.log_pass(delta, elapsed=elapsed, writes=writes, flag=flag)
        status = "written" if writes else "nothing_to_promote"
        logger.info(
            "gardener: {} — {} righe, {} scritture, {} in {:.1f}s",
            store.name, delta.line_count, writes, status, elapsed,
        )
    elif MemoryStore.internal_run_completed(resp):
        # Completata ma con le scritture bloccate o rifiutate: il cursore **non**
        # avanza, altrimenti quelle righe risulterebbero digerite da una passata
        # che non ha prodotto niente, e nessun giro successivo le rivedrebbe.
        logger.warning("gardener: {} ha finito senza scrivere; cursore fermo", store.name)
        status = "no_write"
    else:
        logger.warning("gardener: {} non ha finito; cursore fermo", store.name)
        status = "incomplete"

    _prune_sessions(agent)
    return GardenerOutcome(
        status=status, elapsed=elapsed, lines=delta.line_count, writes=writes
    )


def _timezone_of(agent: Any) -> str | None:
    context = getattr(agent, "context", None)
    return getattr(context, "timezone", None)


def _prune_sessions(agent: Any) -> None:
    from jenny.agent.memory import MemoryStore

    sessions = getattr(agent, "sessions", None)
    sessions_dir = getattr(sessions, "sessions_dir", None)
    if sessions_dir is None:
        return
    pruned = MemoryStore.prune_internal_sessions(sessions_dir, "gardener")
    if pruned and hasattr(agent, "evict_pruned_sessions"):
        agent.evict_pruned_sessions(pruned)
