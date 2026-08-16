"""Context builder for assembling agent prompts."""

import base64
import hashlib
import mimetypes
import platform
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jenny.agent.memory import MemoryStore
from jenny.agent.skills import SkillsLoader
from jenny.config.paths import get_output_path
from jenny.session.goal_state import goal_state_runtime_lines
from jenny.utils.helpers import (
    current_time_str,
    detect_image_mime,
    load_bundled_template,
    merge_message_content,
    truncate_text_to_tokens,
)
from jenny.utils.prompt_templates import render_template

# Fallback quando ContextBuilder è costruito senza config (test, tool isolati):
# stesso valore del default di ``AtlasConfig.max_context_tokens``.
_DEFAULT_WIKI_DIRECTORY_TOKENS = 1200

# Il nome del tool che fa da interruttore ad ``agent/scheduling.md``. Costante e
# non ``CronTool.name``: importare il tool qui tirerebbe dentro tutto il package
# cron, e ``context.py`` lo importa mezzo repo. L'accoppiamento lo tiene fermo un
# test (``test_cron_tool_name_constant_matches``), che ``CronTool`` lo importa
# davvero perché lì costa solo tempo di test.
_CRON_TOOL_NAME = "cron"


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
    # Le versioni *ritirate* di un template, per digest sha256 del testo
    # strippato. Servono perché il riconoscimento qui sopra è un confronto con
    # la copia bundled **corrente**: riscrivere un template scollega ogni
    # installazione seedata con quella precedente e mai toccata da Dream, che
    # da un momento all'altro smette di essere "il modulo vuoto di serie" e
    # diventa "roba scritta dall'utente" — modulo a caselle compreso, e senza
    # nemmeno l'etichetta, perché quel ramo non la mette. Sarebbe disfare
    # ``5bc4d9e`` per chi non ha aggiornato in tempo.
    #
    # Non è una finestra breve: Dream scrive ``USER.md`` solo quando ha un
    # fatto personale da instradare, quindi su un'installazione usata per
    # lavoro di progetto quel file può restare vergine a tempo indeterminato.
    #
    # Chi riscrive un template qui elencato deve aggiungere il digest della
    # versione uscente. Non è un promemoria: ``test_current_user_template_digest_is_pinned``
    # (e il suo gemello per ``AGENTS.md``) fallisce finché non lo si fa.
    _RETIRED_TEMPLATE_DIGESTS: dict[str, frozenset[str]] = {
        # 0.3.0 (8833b94) → 0.7.1: "# User Profile" con i tre blocchi di
        # caselle, "(your name)" e le sezioni fra parentesi.
        "USER.md": frozenset({
            "db2c6d63e0b43e5ac414da85f86454e2614f6524d4ef92a291f11476e6e03deb",
        }),
        # Le tre versioni di "# Agent Instructions", il manuale di cron e
        # heartbeat che si spediva dentro un file dell'utente. Sono tutte e tre
        # candidate vive: un telefono porta per sempre quella che era bundled al
        # *suo* primo avvio, indipendentemente da quanti aggiornamenti ha preso
        # dopo — quel file si crea una volta sola e non si tocca più.
        "AGENTS.md": frozenset({
            # 0.3.0 (8833b94) → 0.6.0. È quella sul Titan 2.
            "a7883c61338446966621d481f996d7585987142461f716f64e04e4d692a6b341",
            # 6c5dba8: + il blocco reminder/monitor. Mai uscita in una release,
            # ma un'installazione da sorgente in quella finestra ce l'ha.
            "7573b397f15350b683bb6e87392d27a479e62bf1894a53fe2a60d29d813106c6",
            # 1f23ef3 (0.6.6) → 0.7.1: + il contratto di silenzio.
            "72d4bd718e70e16b9e6b7f5f9a0dc73a5b34d4a972bb43c0b6ebec5072d280c3",
        }),
    }
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

        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        # ``output_path`` serve anche in modalità orchestratore, dove l'agente
        # non scrive file: è lui a scrivere i prompt dei subagenti con
        # ``spawn``, quindi è lui a dettare loro la destinazione sbagliata se
        # non la conosce. Da qui l'assenza di guardia sul flag.
        parts.append(render_template(
            "agent/tool_contract.md",
            orchestrator=orchestrating,
            output_path=str(get_output_path(_absolute_workspace(root))),
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
        tool_names = self._resolve_tool_names(available_tools)
        if tool_names is None or _CRON_TOOL_NAME in tool_names:
            # v. ``_render_tool_inventory``: workspace di una versione
            # precedente, dove questo template non è ancora stato estratto.
            with suppress(Exception):
                parts.append(render_template("agent/scheduling.md"))

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
        wiki_directory = self.memory.get_wiki_memory_context(self.wiki_directory_max_tokens)
        if wiki_directory:
            memory_sections.append(wiki_directory)
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
        """Get the core identity section."""
        root = workspace or self.workspace
        workspace_path = str(_absolute_workspace(root))
        runtime = f"Android, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
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
        """
        parts = []
        root = workspace or self.workspace

        for filename in self.BOOTSTRAP_FILES:
            file_path = root / filename
            if not file_path.exists():
                continue
            content = file_path.read_text(encoding="utf-8")
            if not self._is_template_content(content, filename):
                parts.append(f"## {filename}\n\n{content}")
                continue
            if filename in self._BOOTSTRAP_SKIP_IF_TEMPLATE:
                continue
            parts.append(f"## {filename}\n\n{self._BOOTSTRAP_TEMPLATE_NOTICE}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to a bundled template (user hasn't customized it).

        "Bundled" include le versioni ritirate: quello che si vuole sapere qui
        è se il file l'ha scritto l'utente, e un template che spediva una
        release fa non l'ha scritto più di quello di oggi (v.
        ``_RETIRED_TEMPLATE_DIGESTS``).
        """
        stripped = content.strip()
        tpl = load_bundled_template(template_path)
        if tpl is not None and stripped == tpl.strip():
            return True
        retired = ContextBuilder._RETIRED_TEMPLATE_DIGESTS.get(template_path)
        if not retired:
            return False
        return hashlib.sha256(stripped.encode("utf-8")).hexdigest() in retired

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
