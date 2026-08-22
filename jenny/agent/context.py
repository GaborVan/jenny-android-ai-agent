"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from loguru import logger

from jenny.agent.memory import MemoryStore
from jenny.agent.skills import SkillsLoader
from jenny.config.paths import get_output_path
from jenny.session.goal_state import goal_state_runtime_lines
from jenny.session.keys import is_project_session_key
from jenny.utils.android_assets import (
    _RETIRED_TEMPLATE_DIGESTS,
    normalized_template_text,
    template_digest,
)
from jenny.utils.helpers import (
    current_time_str,
    detect_image_mime,
    load_bundled_template,
    merge_message_content,
    truncate_text_to_tokens,
)
from jenny.utils.prompt_templates import render_template
from jenny.utils.wiki_paths import (
    LEGACY_WIKI_SCHEMA_FILENAME,
    WIKI_SCHEMA_FILENAME,
    is_wiki_root,
    wiki_schema_file,
)

# Fallback quando ContextBuilder è costruito senza config (test, tool isolati):
# stesso valore del default di ``AtlasConfig.max_context_tokens``.
_DEFAULT_WIKI_DIRECTORY_TOKENS = 1200

# Il nome del tool che fa da interruttore ad ``agent/scheduling.md``. Costante e
# non ``CronTool.name``: importare il tool qui tirerebbe dentro tutto il package
# cron, e ``context.py`` lo importa mezzo repo. L'accoppiamento lo tiene fermo un
# test (``test_cron_tool_name_constant_matches``), che ``CronTool`` lo importa
# davvero perché lì costa solo tempo di test.
_CRON_TOOL_NAME = "cron"


def _turn_is_writable() -> bool:
    """Se il turno in corso può cambiare qualcosa.

    Wrapper di una riga sopra ``current_workspace_scope`` per una ragione sola:
    ``ContextBuilder`` costruisce il prompt anche fuori da un turno (test,
    ispezione, sessioni interne), e là non c'è nessuno scope legato — che vuol
    dire scrivibile, non il contrario.
    """
    from jenny.security.workspace_access import current_workspace_scope

    scope = current_workspace_scope()
    return scope is None or scope.writable


def _absolute_workspace(root: Path) -> Path:
    """La radice del workspace in forma assoluta e normalizzata.

    Ogni path che finisce nel prompt passa di qui, perché un percorso relativo
    o non espanso è un percorso che il modello detta a un tool (o a un
    subagente) e che poi non esiste. ``expanduser()`` sta dentro un try perché
    su Android può sollevare — ``HOME`` non è garantito e ``pwd`` non conosce
    l'uid dell'app: in quel caso si tiene il path com'è, che è comunque
    migliore di un prompt senza percorso.
    """
    try:
        expanded = root.expanduser()
    except (RuntimeError, OSError):
        expanded = root
    return expanded.resolve()


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    # I file che dicono **chi e' Jenny e chi sei tu**: vengono sempre dalla
    # radice dell'installazione, mai dalla cartella di un progetto.
    #
    # Senza questa distinzione, legare uno scope li faceva cercare nella
    # cartella del progetto, dove non ci sono, e ``_load_bootstrap_files``
    # saltava i file assenti in silenzio: Jenny perdeva personalita' e tutto
    # quello che sa dell'utente, senza un errore e senza una riga di log.
    # ``AGENTS.md`` invece resta legato allo scope apposta — sono *le istruzioni
    # di quel posto di lavoro*, ed e' il file che ogni progetto ha di suo.
    #
    # ``MEMORY.md`` non e' in questo elenco perche' non passa da qui: lo legge
    # ``MemoryStore``, costruito una volta sulla radice dell'installazione
    # (v. :meth:`__init__`). Prima era giusto per caso; ora c'e' un test che lo
    # tiene fermo insieme a questi due.
    _IDENTITY_FILES = frozenset({"SOUL.md", "USER.md"})
    # File di bootstrap da omettere del tutto quando sono ancora il template
    # intatto. ``USER.md`` e ``AGENTS.md`` intatti non dicono niente né
    # sull'utente né sul workspace: sono l'impalcatura che spiega dove va cosa,
    # e le versioni ritirate erano perfino peggio (un modulo a caselle il primo,
    # un manuale di cron scritto da noi il secondo). È lo stesso caso di
    # ``MEMORY.md`` e riceve la stessa risposta: si salta.
    #
    # ``AGENTS.md`` ci è entrato con ``roadmap/agents-md-ownership.md``, che ha
    # spostato la sua metà "di sistema" in ``agent/scheduling.md`` — dove un
    # aggiornamento arriva davvero, perché ``agent/**`` si riscrive a ogni boot
    # mentre i file dell'utente si creano una volta sola. Quel che resta è un
    # segnaposto, e un segnaposto nel prompt è solo contesto pagato a vuoto.
    #
    # ``SOUL.md`` no: il suo template non è un segnaposto, è l'identità di
    # Jenny, che non è scritta in nessun altro punto del prompt. Ometterla
    # perché nessuno l'ha ancora modificata toglierebbe personalità a ogni
    # installazione nuova — una regressione, non una correzione. Resta nel
    # prompt, etichettata per quello che è, così che il modello non la citi
    # come preferenza dell'utente.
    _BOOTSTRAP_SKIP_IF_TEMPLATE = frozenset({"USER.md", "AGENTS.md"})
    # Le versioni ritirate di quei template le elenca
    # ``_RETIRED_TEMPLATE_DIGESTS`` (``jenny/utils/android_assets.py``), accanto
    # alle due liste che dicono quali template esistono e che politica riceve
    # ciascuno. Definizione una sola: i consumatori sono due, questo e la
    # riscrittura al boot, e tenerne due copie allineate è esattamente il guasto
    # che quel registro esiste per evitare.
    #
    # La formula "still matches the template shipped with the app" è falsa per
    # una versione ritirata, ed è il motivo per cui ``97d7b38`` aveva lasciato
    # fuori ``AGENTS.md``. Il problema non è stato risolto: è sparito. Un file in
    # ``_BOOTSTRAP_SKIP_IF_TEMPLATE`` non arriva mai a questo ramo, e ``SOUL.md``
    # — l'unico che può ancora essere etichettato — di digest ritirati non ne ha,
    # quindi l'avviso esce solo quando è vero alla lettera. Non riscrivere il
    # testo per un caso che non può presentarsi.
    _BOOTSTRAP_TEMPLATE_NOTICE = (
        "[Unmodified default — this file still matches the template shipped with the app; "
        "the user has not written any of it. Nothing below states a user preference.]"
    )
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_TOKENS = 8_000  # hard cap on recent history section size (tokens)
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"

    def __init__(
        self,
        workspace: Path,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
        orchestrator: bool = False,
        available_tools: Callable[[], list[str]] | None = None,
        wiki_directory_max_tokens: int | None = None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        # Callable e non lista: il registry non esiste ancora quando ``AgentLoop``
        # costruisce questo oggetto, e comunque i tool delle Jenny App cambiano
        # a runtime. Chiuderlo su una lista significherebbe pubblicare un
        # inventario vecchio, cioe rifare il difetto che deve chiudere.
        self._available_tools = available_tools
        # Modalita orchestratore: i template ricevono il flag e omettono le
        # istruzioni sui tool che in quello scope non esistono. Un prompt che
        # descrive tool assenti non e solo contesto sprecato: invita il modello a
        # chiamarli.
        self.orchestrator = orchestrator
        # Tetto del blocco "Wiki Directory" compilato da Atlas. ``None`` lascia
        # il default dello schema; il valore reale arriva da AgentLoop.from_config.
        self.wiki_directory_max_tokens = wiki_directory_max_tokens or _DEFAULT_WIKI_DIRECTORY_TOKENS
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        channel: str | None = None,
        session_summary: str | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        available_tools: list[str] | None = None,
        orchestrator: bool | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills.

        ``available_tools`` sono i tool del *turno*. Passarli esplicitamente e
        l'unico modo perche l'inventario descriva un registry sostituito (Dream,
        Atlas) invece di quello del loop; la callable del costruttore resta il
        default per chi un registry per-turno non ce l'ha.

        ``orchestrator`` e per-turno per lo stesso motivo, e per un difetto
        gemello: era un flag del costruttore, quindi Dream e Atlas — che girano
        con un registry proprio e con la scrittura come unico mestiere — si
        vedevano recapitare il blocco che dice "non puoi scrivere file, delega
        con ``spawn``". Nessuno dei due ha ``spawn``.
        """
        orchestrating = self.orchestrator if orchestrator is None else orchestrator
        root = workspace or self.workspace
        parts = [self._get_identity(channel=channel, workspace=root, orchestrating=orchestrating)]

        # La cartella del turno e' una wiki: allora e' *quella* la pianta che
        # vale, non le convenzioni del workspace.
        #
        # La domanda e' sulla **cartella** e non sulla sessione apposta: chi ha
        # bisogno di questa risposta e' anche il subagent, che riceve la radice
        # dal ``WorkspaceScope`` del turno e la chiave di sessione non ce l'ha
        # mai. Ed e' il subagent ad aver scritto, il 21/08, il file di prova in
        # ``wikis/<nome>/output/`` — obbedendo alla lettera al suo prompt, che
        # gli dava le regole del workspace applicate a una cartella di progetto.
        # La rubrica qui sotto invece si chiude sulla *sessione*, perche' quella
        # e' una domanda su chi sta parlando, non su dove si lavora.
        in_project = is_wiki_root(root)
        if in_project:
            with suppress(Exception):  # workspace sincronizzato da una versione precedente
                parts.append(render_template(
                    "agent/project.md",
                    project_path=str(_absolute_workspace(root)),
                    # La politica di cattura vale per **questo** turno, quello in
                    # cui c'e' un utente che dice qualcosa. Un subagent riceve lo
                    # stesso file con ``capture=False`` (v. ``agent/subagent.py``):
                    # non ha un utente, e la sua materia prima e' il prompt che
                    # gli ha scritto l'agente principale. Se catturasse, nel
                    # diario finirebbe il suo ragionamento intermedio — e il
                    # diario e' l'ingresso del giardiniere, quindi quel rumore
                    # diventerebbe pagine.
                    #
                    # **In sola lettura la sezione non si rende affatto**, e non
                    # e' un'ottimizzazione: misurato sul telefono il 22/08, con la
                    # regola presente l'agente ha provato **due volte** a
                    # catturare — e al secondo tentativo ha scritto al subagent
                    # «se il tool di scrittura ti e' negato, riprova con
                    # apply_patch». Il divieto di ``agent/readonly.md`` c'era, ed
                    # e' anche piu' in basso (cioe' vince, v. il test sull'ordine);
                    # non e' bastato. Dare un ordine e poi vietarlo due paragrafi
                    # dopo e' un invito a cercare la scappatoia: meglio non darlo.
                    capture=_turn_is_writable(),
                ))

        # Il blocco sta **prima** del bootstrap, al contrario di
        # ``agent/scheduling.md``: li' la prosa di sistema deve vincere su un
        # ``AGENTS.md`` vecchio, qui deve perdere. L'``AGENTS.md`` di un
        # progetto e' il posto in cui tu — o Jenny — scrivete come si lavora
        # *in questo* progetto, e un'eccezione scritta li' non serve a niente se
        # la regola generale la segue e la sovrascrive.
        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        # ``output_path`` serve anche in modalità orchestratore, dove l'agente
        # non scrive file: è lui a scrivere i prompt dei subagenti con
        # ``spawn``, quindi è lui a dettare loro la destinazione sbagliata se
        # non la conosce. Da qui l'assenza di guardia sul flag.
        #
        # ``has`` è il gate per-tool, e arriva fin qui perché per tre versioni non
        # c'era: questo template si rendeva intero con il solo flag
        # ``orchestrator``, che dice come si lavora e non quali tool esistono. Chi
        # lo pagava era Dream (``orchestrator=False``, quattro tool in tutto), che
        # si prendeva le sezioni su ``python_exec``, ``grep``, i tool web,
        # ``download_file`` e ``message`` — ~6 kB di istruzioni su tool assenti, e
        # non solo contesto sprecato: fra quelle righe c'era "deleting is the one
        # file operation that needs ``python_exec``", detta all'unico agente a cui
        # ``dream_review.md`` chiede di cancellare e che ``python_exec`` non ce
        # l'ha.
        #
        # Stessa semantica del gate di ``agent/scheduling.md`` poco sotto: ``None``
        # vuol dire "non lo so" (nessun registry per-turno, nessuna callable), non
        # "il tool non c'è", e in quel caso ``has`` è vera per tutto — il prompt
        # resta byte-identico a quello di prima.
        tool_names = self._resolve_tool_names(available_tools)
        parts.append(render_template(
            "agent/tool_contract.md",
            orchestrator=orchestrating,
            has=self._tool_predicate(tool_names),
            output_path=str(get_output_path(_absolute_workspace(root))),
            # Due sezioni di quel template parlano del workspace come se fosse
            # sempre la cartella di lavoro: ``output/`` come destinazione di quel
            # che si produce, e i quattro documenti alla radice. Dentro una wiki
            # sono due affermazioni false, e la prima e' quella che ha spedito il
            # file di prova in ``wikis/<nome>/output/``. La pianta giusta la dice
            # ``agent/project.md``, quindi qui quelle due sezioni si spengono
            # invece di essere riscritte: un solo proprietario per regola.
            project=in_project,
        ))

        # Dove va un lavoro ricorrente: heartbeat, `reminder` o `monitor`. Era
        # nel template di ``AGENTS.md``, cioè in un file che si crea al primo
        # avvio e non si aggiorna mai più — su un telefono aggiornato da mesi
        # restava il testo della versione in cui era stato installato.
        #
        # Guardia sul tool e non sul modo: l'orchestratore `cron` ce l'ha
        # (``CronTool._scopes``), Dream e Atlas no (``build_dream_tools``), e
        # fin qui si vedevano recapitare l'istruzione di schedulare con un tool
        # che il loro registry non contiene — lo stesso difetto che il parametro
        # `orchestrator` per-turno è nato per chiudere. ``None`` vuol dire "non
        # lo so" (nessun registry per-turno, nessuna callable), non "il tool non
        # c'è": si rende, come faceva ``AGENTS.md``.
        #
        # La posizione è dopo il blocco di bootstrap, e non è cosmesi: v.
        # ``_render_tool_inventory``, la prosa più vicina alla fine è quella che
        # il modello segue quando due istruzioni si contraddicono. Su
        # un'installazione dove l'utente ha scritto *sopra* il vecchio testo di
        # sistema — l'unico caso che nessuna migrazione può raggiungere — è la
        # sola cosa che decide la contraddizione dalla parte giusta.
        if tool_names is None or _CRON_TOOL_NAME in tool_names:
            # v. ``_render_tool_inventory``: workspace di una versione
            # precedente, dove questo template non è ancora stato estratto.
            with suppress(Exception):
                parts.append(render_template("agent/scheduling.md"))

        # Sola lettura: **una riga nel prompt qui se la guadagna**, al contrario
        # del rifiuto dei promemoria (passo 3), che sta solo nel tool. Il
        # criterio è lo stesso e decide al contrario: una regola merita spazio
        # nel blocco quando ci sbatteresti addosso di continuo e ti costringe a
        # ripianificare. Un promemoria è raro e sta in piedi da solo; scrivere è
        # quel che si fa a ogni turno, e scoprirlo a metà lavoro butta la
        # chiamata *e* il piano.
        #
        # Sta in fondo, come ``agent/scheduling.md`` e per la stessa ragione: è
        # la prosa più vicina alla fine a decidere le contraddizioni, e questa
        # deve vincere su qualunque istruzione più su che dica di scrivere —
        # comprese quelle di un ``AGENTS.md`` di progetto.
        if not _turn_is_writable():
            with suppress(Exception):  # workspace sincronizzato da una versione precedente
                parts.append(render_template("agent/readonly.md"))

        if orchestrating:
            parts.append(render_template("agent/orchestrator.md"))

        # Il blocco memoria ha due sottosezioni con due proprietari distinti:
        # "Long-term Memory" (MEMORY.md, scritto da Dream) e "Wiki Directory"
        # (memory/WIKI.md, scritto da Atlas). Vanno composte in modo
        # indipendente: annidare la seconda dentro la guardia della prima
        # farebbe sparire la rubrica ogni volta che MEMORY.md è ancora il
        # template intatto. Heading unico e ordine fisso tengono stabile il
        # prefisso del prompt per la cache del provider.
        memory_sections: list[str] = []
        memory = self.memory.get_memory_context()
        if memory and not self._is_template_content(self.memory.read_memory(), "memory/MEMORY.md"):
            memory_sections.append(memory)
        # La rubrica di Atlas elenca **tutte** le wiki, piu' persone, progetti e
        # piante. Nella chat personale e' portante — un indice che nessuno sa
        # esistere non viene mai aperto — ma dentro un progetto risponde a una
        # domanda gia' risposta: il progetto l'hai scelto tu prima che il turno
        # cominciasse. Peggio, elenca otto posti in cui la scrittura rimbalza
        # (v. il confine del passo 1) e ci porta dentro la vita privata.
        #
        # Chiusa sulla **sessione** e non sulla cartella: la domanda e' "chi sta
        # parlando", non "dove si lavora". ``MEMORY.md`` qui sopra resta invece
        # in tutti e due i casi, ed e' la stessa riga di confine dell'1.2 — chi
        # sei viaggia, dove altro lavori no.
        if not is_project_session_key(session_key or ""):
            wiki_directory = self.memory.get_wiki_memory_context(self.wiki_directory_max_tokens)
            if wiki_directory:
                memory_sections.append(wiki_directory)
        # Terza sottosezione, e la piu' economica: una riga che dice che il tier
        # freddo esiste. Senza, l'archivio della fase 2 sarebbe indistinguibile
        # da una cancellazione dal punto di vista di chi deve rispondere — ed e'
        # la stessa ragione per cui la rubrica di Atlas sta qui sopra.
        archive = self.memory.get_archive_context()
        if archive:
            memory_sections.append(archive)
        if memory_sections:
            parts.append("# Memory\n\n" + "\n\n".join(memory_sections))

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        from jenny.apps.summary import build_apps_summary

        apps_summary = build_apps_summary(root)
        if apps_summary:
            parts.append(render_template("agent/apps_section.md", apps_summary=apps_summary))

        if include_memory_recent_history:
            entries = self.memory.read_recent_history_for_prompt(
                since_cursor=self.memory.get_last_dream_cursor(),
                session_key=session_key,
            )
            if entries:
                capped = entries[-self._MAX_RECENT_HISTORY:]
                history_text = "\n".join(
                    f"- [{e['timestamp']}] {e['content']}" for e in capped
                )
                history_text = truncate_text_to_tokens(history_text, self._MAX_HISTORY_TOKENS)
                parts.append("# Recent History\n\n" + history_text)

        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")

        if inventory := self._render_tool_inventory(available_tools, orchestrating):
            parts.append(inventory)

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _tool_predicate(tool_names: list[str] | None) -> Callable[[str], bool]:
        """``has('python_exec')`` per i template, chiuso sui nomi di questo turno.

        Una funzione e non un insieme passato al template: Jinja2 su un ``in`` con
        una variabile assente non solleva, la valuta falsa — cioè un errore di
        battitura nel nome della variabile spegnerebbe in silenzio ogni sezione
        del contratto. Una callable mancante invece fa fallire il render, e un
        render fallito lo si vede.

        ``None`` (nessun registry per-turno e nessuna callable) vuol dire "non lo
        so" e risponde sì a tutto: un percorso che non sa quali tool ha deve
        vedere il contratto intero, non zero sezioni. Stessa scelta del gate di
        ``agent/scheduling.md``.
        """
        if tool_names is None:
            return lambda _name: True
        available = set(tool_names)
        return lambda name: name in available

    def _resolve_tool_names(self, available_tools: list[str] | None) -> list[str] | None:
        """I nomi del turno se ci sono, altrimenti quelli del loop."""
        if available_tools is not None:
            return sorted(available_tools)
        if self._available_tools is None:
            return None
        try:
            return sorted(self._available_tools())
        except Exception:
            return None

    def _render_tool_inventory(
        self, available_tools: list[str] | None = None, orchestrating: bool | None = None,
    ) -> str | None:
        """L'elenco autoritativo dei tool, in coda a tutto il resto.

        Un prompt e cucito da pezzi scritti in momenti diversi — identita,
        contratto dei tool, skill, documenti dell'utente — e nessuno di quei
        pezzi sa quali tool esistono davvero in questo processo. Bastano due
        frasi in disaccordo per farne vincere una a caso: e successo con
        ``grep``, che il contratto dichiarava assente e una skill mostrava in
        cinque esempi.

        Sta in fondo perche la prosa piu vicina alla fine e quella che il
        modello segue quando due istruzioni si contraddicono, e viene dal
        registry perche una lista scritta a mano invecchierebbe come tutte le
        altre.
        """
        names = self._resolve_tool_names(available_tools)
        if not names:
            return None
        try:
            return render_template(
                "agent/tool_inventory.md",
                tool_names=names,
                orchestrator=self.orchestrator if orchestrating is None else orchestrating,
                strip=True,
            )
        except Exception:
            # Workspace di una versione precedente, dove questo template non e
            # ancora stato estratto: si perde l'inventario, non il prompt.
            return None

    def _get_identity(
        self,
        channel: str | None = None,
        workspace: Path | None = None,
        orchestrating: bool | None = None,
    ) -> str:
        """Get the core identity section.

        **Due radici, come in :meth:`_load_bootstrap_files`.** ``workspace_path``
        e' la cartella del turno — quella del progetto, quando la sessione ne ha
        uno legato — ma ``memory/``, ``history.jsonl`` e ``skills/`` stanno
        nell'installazione e basta: composti su ``workspace_path`` erano tre
        percorsi **falsi** nelle prime dieci righe del prompt di ogni turno di
        progetto (``.../wikis/<nome>/memory/MEMORY.md``, ``.../wikis/<nome>/skills``).
        Era il resto del lavoro dell'1.2, che aveva sdoppiato la radice dei file
        di bootstrap e non questa. Fuori da un progetto le due radici coincidono
        e il prompt resta byte-identico.
        """
        root = workspace or self.workspace
        workspace_path = str(_absolute_workspace(root))
        runtime = f"Android, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            install_path=str(_absolute_workspace(self.workspace)),
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md"),
            channel=channel or "",
            orchestrator=self.orchestrator if orchestrating is None else orchestrating,
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        sender_id: str | None = None,
        supplemental_lines: Sequence[str] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block appended after user content."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if sender_id:
            lines += [f"Sender ID: {sender_id}"]
        if supplemental_lines:
            lines.extend(supplemental_lines)
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines) + "\n" + ContextBuilder._RUNTIME_CONTEXT_END

    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load all bootstrap files from workspace.

        Un file di bootstrap ancora identico al template che il primo avvio ha
        copiato nel workspace non è roba scritta dall'utente, e finora entrava
        nel prompt come se lo fosse — mentre ``MEMORY.md`` ha la sua guardia
        (``_is_template_content``, sopra). Qui però la risposta giusta non è la
        stessa per tutti e tre, perché i tre template non sono la stessa cosa:
        vedi ``_BOOTSTRAP_SKIP_IF_TEMPLATE``.

        **Due radici, non una.** ``workspace`` e' la cartella del turno — quella
        del progetto, quando la sessione ne ha uno legato — e da li' viene
        ``AGENTS.md``, che e' le istruzioni di *quel* posto. L'identita'
        (:attr:`_IDENTITY_FILES`) viene invece sempre dalla radice
        dell'installazione: e' chi e' Jenny e chi e' l'utente, e non cambia
        perche' si sta lavorando dentro una cartella diversa. Senza scope legato
        le due radici coincidono e non cambia niente.
        """
        parts = []
        project_root = workspace or self.workspace

        for filename in self.BOOTSTRAP_FILES:
            # Il ramo si sceglie sul **nome**, non sulla radice: senza uno scope
            # legato le due radici sono lo stesso oggetto, e una guardia
            # sull'identita' del path mandava anche ``SOUL.md`` e ``USER.md``
            # dentro la ricerca del file di istruzioni — cioe' via dal prompt.
            if filename in self._IDENTITY_FILES:
                file_path = self.workspace / filename
            else:
                file_path = self._instructions_file(project_root)
            if file_path is None or not file_path.exists():
                continue
            # Il nome vero del file letto, che dentro una wiki puo' essere
            # ``CLAUDE.md``: sotto un nome che sul disco non c'e', ogni ``edit``
            # che il modello prova manca il bersaglio.
            filename = file_path.name
            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                # File esistente ma senza contenuto: un heading con sotto il
                # nulla, pagato a ogni turno e senza nemmeno dire cosa manca.
                # Non è ipotetico: ``agent/dream_review.md`` ordina di
                # cancellare "l'introduzione che spiega a cosa serve il file",
                # e il template di ``USER.md`` è fatto solo di quella — una
                # revisione che lo esegue alla lettera lascia il file vuoto.
                #
                # La guardia sta *prima* di ``_is_template_content`` perché
                # quel confronto legge il vuoto in due modi opposti a seconda
                # di com'è il template bundled: oggi ``False`` (vuoto = scritto
                # dall'utente, ramo che lo inietta nudo), e ``True`` appena un
                # template bundled diventa vuoto a sua volta (ramo che lo
                # etichetta come default intatto, avviso senza testo sotto).
                # Qui a monte le due letture sono entrambe innocue: comunque la
                # si legga, un file vuoto nel prompt non ci entra.
                continue
            if not self._is_template_content(content, filename):
                parts.append(f"## {filename}\n\n{content}")
                continue
            if filename in self._BOOTSTRAP_SKIP_IF_TEMPLATE:
                continue
            parts.append(f"## {filename}\n\n{self._BOOTSTRAP_TEMPLATE_NOTICE}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def _instructions_file(self, root: Path) -> Path | None:
        """Il file di istruzioni di ``root``: ``AGENTS.md``, e nient'altro.

        Il ripiego sul nome vecchio e' stato tolto nel **7.5**: la migrazione
        rinomina le wiki a ogni avvio (``utils/wiki_migration.py``), quindi due
        nomi accettati qui sarebbero due nomi da tenere allineati per sempre in
        quattro lettori.

        Resta il caso in cui l'utente continua a modificare il file col nome
        vecchio senza accorgersi che non entra piu' nel prompt: lo si dice, ed e'
        l'unico segnale che lo distingue da un file inerte.
        """
        if not is_wiki_root(root):
            return root / WIKI_SCHEMA_FILENAME
        leftover = root / LEGACY_WIKI_SCHEMA_FILENAME
        if leftover.is_file():
            logger.warning(
                "{}: c'e' ancora {} — non entra nel prompt, e la migrazione lo rinomina "
                "al prossimo avvio", root, leftover.name,
            )
        return wiki_schema_file(root)

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to a bundled template (user hasn't customized it).

        "Bundled" include le versioni ritirate: quello che si vuole sapere qui
        è se il file l'ha scritto l'utente, e un template che spediva una
        release fa non l'ha scritto più di quello di oggi (v.
        ``_RETIRED_TEMPLATE_DIGESTS``).

        La normalizzazione dei due lati sta in ``normalized_template_text``, con
        la riscrittura del boot: un BOM UTF-8 sopravvive a ``strip()`` e faceva
        smettere di combaciare un template che l'utente non ha mai scritto.
        """
        stripped = normalized_template_text(content)
        tpl = load_bundled_template(template_path)
        if tpl is not None and stripped == normalized_template_text(tpl):
            return True
        retired = _RETIRED_TEMPLATE_DIGESTS.get(template_path)
        if not retired:
            return False
        return template_digest(content) in retired

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        sender_id: str | None = None,
        session_summary: str | None = None,
        session_metadata: Mapping[str, Any] | None = None,
        current_runtime_lines: Sequence[str] | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        available_tools: list[str] | None = None,
        orchestrator: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        root = workspace or self.workspace
        extra = [
            *goal_state_runtime_lines(session_metadata),
        ]
        if current_runtime_lines:
            extra.extend(line for line in current_runtime_lines if line)
        runtime_ctx = self._build_runtime_context(
            channel,
            chat_id,
            self.timezone,
            sender_id=sender_id,
            supplemental_lines=extra or None,
        )
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        # Runtime context is appended to keep the user-content prefix stable
        # for prompt-cache hits (the context changes every turn due to time).
        if isinstance(user_content, str):
            merged = f"{user_content}\n\n{runtime_ctx}"
        else:
            merged = user_content + [{"type": "text", "text": runtime_ctx}]
        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    channel=channel,
                    session_summary=session_summary,
                    workspace=root,
                    include_memory_recent_history=include_memory_recent_history,
                    session_key=session_key,
                    available_tools=available_tools,
                    orchestrator=orchestrator,
                ),
            },
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]
