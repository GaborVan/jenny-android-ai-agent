"""La passata del giardiniere: la cassetta chiusa, il prompt, il cursore.

Passo **T4.2** di ``roadmap/taccuino-passi.md``.

Il gruppo che conta è ``TestTheToolbox``, e la ragione va scritta: **il
confinamento di un turno interno è il registry, non lo scope.** Un turno interno
gira su ``INTERNAL_CHANNEL``, e ``WorkspaceScopeResolver.for_turn`` per ogni
canale che non sia la WebUI restituisce lo scope *di default* — l'intera
installazione scrivibile. Quel che tiene il giardiniere dentro ``wiki/`` è la
cassetta dei tool, e nient'altro. Da cui due test e non uno: la allowlist di
scrittura, **e l'elenco esatto dei tool** — perché uno ``spawn_subagent`` nella
cassetta sarebbe una scrittura ovunque per interposta persona, e non farebbe
cadere nessun test sulla allowlist.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent.gardener import GardenerStore, run_gardener
from jenny.agent.gardener_state import GardenerState, read_state
from jenny.agent.tools.file_state import FileStates
from jenny.security.workspace_access import (
    bind_workspace_scope,
    default_workspace_scope,
    reset_workspace_scope,
)

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

_DAY = date(2026, 8, 23)


def _project(workspace: Path, name: str = "viaggio", *, journal: bool = True) -> Path:
    root = workspace / "wikis" / name
    (root / "wiki").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text("# viaggio\n\n## Pages\n\n*(none yet)*\n", "utf-8")
    (root / "AGENTS.md").write_text("---\nid: abc123abc123\n---\n\n# viaggio\n", "utf-8")
    if journal:
        d = root / "raw" / "journal"
        d.mkdir(parents=True)
        (d / "20260822.md").write_text(
            "# 2026-08-22\n\n- 09:00 — il furgone ha le gomme da cambiare\n"
            "- 09:10 — si parte il 14\n",
            "utf-8",
        )
    return root


def _store(workspace: Path, name: str = "viaggio") -> GardenerStore:
    store = GardenerStore.for_project(workspace, name)
    assert store is not None
    store._today = lambda: _DAY  # type: ignore[method-assign]
    return store


class _FakeAgent:
    """Agente minimale: registra la chiamata e restituisce un esito pilotato."""

    def __init__(self, sessions_dir: Path, *, stop_reason: str = "completed") -> None:
        self.context = SimpleNamespace(memory=None, timezone="Europe/Rome")
        self.sessions = SimpleNamespace(sessions_dir=sessions_dir)
        self.calls: list[dict] = []
        self._stop_reason = stop_reason
        self.evicted: list[str] = []
        self.snapshots: list[str] = []
        self.reply = "NOTHING TO FLAG"

    async def take_snapshot(self, trigger: str) -> bool:
        self.snapshots.append(trigger)
        return True

    async def process_direct(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(
            metadata={"_stop_reason": self._stop_reason}, usage={}, content=self.reply,
        )

    def evict_pruned_sessions(self, keys) -> None:
        self.evicted.extend(keys)


class _ExplodingAgent(_FakeAgent):
    async def process_direct(self, prompt: str, **kwargs):
        raise RuntimeError("provider is down")


def _states(*, attempted: int, ok: int) -> FileStates:
    states = FileStates()
    for _ in range(attempted):
        states.record_write_attempt()
    for _ in range(ok):
        states.record_write(Path("/tmp/whatever"))
    return states


def _with_states(store: GardenerStore, states: FileStates) -> GardenerStore:
    store.build_tools = lambda: SimpleNamespace(file_states=states)  # type: ignore[method-assign]
    return store


# ── Il bersaglio ─────────────────────────────────────────────────────────────


class TestTheTarget:
    def test_a_folder_that_is_not_a_project_has_no_gardener(self, tmp_path):
        (tmp_path / "wikis" / "appunti").mkdir(parents=True)
        assert GardenerStore.for_project(tmp_path, "appunti") is None

    def test_a_missing_folder_is_not_an_error(self, tmp_path):
        assert GardenerStore.for_project(tmp_path, "mai-esistito") is None

    @pytest.mark.parametrize("name", ["..", "../..", "../altro"])
    def test_a_name_cannot_climb_out_of_the_projects_folder(self, tmp_path, name):
        """Il nome arriva da una chiave di sessione o da un argomento di comando,
        cioè in ultima analisi da un client: un ``..`` non deve poter portare la
        passata fuori da ``wikis/``."""
        _project(tmp_path)
        assert GardenerStore.for_project(tmp_path, name) is None

    def test_a_workspace_reachable_under_two_names_still_gives_one_path(self, tmp_path):
        """**Il difetto del 23/08, sul telefono.** Su Android la dir dati e'
        raggiungibile come ``/data/user/0/<pkg>`` e ``Path.resolve()`` la riscrive
        in ``/data/data/<pkg>``. Il progetto arrivava risolto e il workspace no,
        quindi ``relative_to`` alzava ``ValueError`` e il prompt diceva
        ``zz-t4/wiki/`` invece di ``wikis/zz-t4/wiki/``: il modello ha scritto
        quattro pagine perfette e sono state **rifiutate tutte**.

        Qui la doppia via e' un symlink, che e' la stessa cosa vista da macOS.
        """
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        _project(real, "zz-t4")

        store = GardenerStore.for_project(link, "zz-t4")

        assert store is not None
        assert store.rel_root == "wikis/zz-t4"

    def test_paths_in_the_prompt_are_relative_to_the_workspace(self, tmp_path):
        """Non è estetica: la base dei percorsi relativi è ``project_path`` dello
        scope legato, che per un turno interno è la radice dell'installazione. E
        un assoluto verrebbe rifiutato comunque — su Android la dir dati ha due
        nomi e la allowlist ne conosce uno (la trappola già pagata da Atlas)."""
        _project(tmp_path)
        assert _store(tmp_path).rel_root == "wikis/viaggio"


# ── La cassetta, cioè il confinamento ────────────────────────────────────────


class TestTheToolbox:
    @pytest.fixture
    def bound(self, tmp_path):
        """Lega lo scope di **default**, come in produzione.

        È il punto del test: un turno interno non ha lo scope del progetto, ha
        quello dell'installazione. Legarlo qui vuol dire provare la cassetta
        nelle condizioni in cui gira davvero, invece di provarla in una
        situazione più stretta di quella vera.
        """
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        yield
        reset_workspace_scope(token)

    def test_the_toolbox_holds_exactly_these_tools(self, tmp_path, bound):
        """**Una porta nella cassetta è una via d'uscita.** ``spawn_subagent``,
        ``python_exec``, ``message``: uno solo di questi e la superficie chiusa
        non esiste più, per interposta persona — e nessun test sulla allowlist
        di scrittura se ne accorgerebbe. Da cui l'elenco, per nome.

        ``journal_append`` è entrato in T6.3, ed è l'unica scrittura fuori da
        ``wiki/`` che si concede. È ammesso per una ragione precisa e non per
        comodità: **non può violare la regola che protegge**. Appende in coda per
        costruzione — un solo file possibile, nessun modo di riscrivere una riga —
        quindi non tocca la fonte da cui la passata sta promuovendo. Un tool che
        potesse riscrivere il diario non entrerebbe qui nemmeno se servisse.
        """
        _project(tmp_path)
        tools = _store(tmp_path).build_tools()

        assert set(tools.tool_names) == {
            "read_file", "list_dir", "find_files", "grep",
            "write_file", "edit_file", "apply_patch",
            "journal_append",
        }

    @pytest.mark.parametrize("door", ["spawn_subagent", "python_exec", "message", "cron"])
    def test_the_toolbox_has_no_door(self, tmp_path, bound, door):
        """Il test sopra cadrebbe comunque, ma dice «l'insieme è cambiato» e non
        «è entrata una via d'uscita». Questo nomina le porte, così il giorno che
        una compare il messaggio dice cosa è successo."""
        _project(tmp_path)

        assert door not in set(_store(tmp_path).build_tools().tool_names)

    async def test_it_writes_a_page(self, tmp_path, bound):
        _project(tmp_path)
        tools = _store(tmp_path).build_tools()

        out = await tools.get("write_file").execute(
            path="wikis/viaggio/wiki/furgone.md", content="---\nstate: open\n---\n\n# Furgone\n"
        )

        assert (tmp_path / "wikis" / "viaggio" / "wiki" / "furgone.md").is_file(), out

    @pytest.mark.parametrize("path,why", [
        ("wikis/viaggio/raw/journal/20260822.md", "il diario è l'input, ed è append-only"),
        ("wikis/viaggio/raw/research/nota.md", "raw/ è verbatim per definizione"),
        ("wikis/viaggio/AGENTS.md", "le premesse le cambia l'utente"),
        ("wikis/viaggio/log/20260823.md", "il log lo scrive il codice"),
        ("wikis/viaggio/audit/nota.md", "audit/ è il canale dell'umano"),
        ("wikis/altro/wiki/pagina.md", "un altro progetto"),
        ("memory/MEMORY.md", "la memoria personale"),
    ])
    async def test_everything_else_is_refused(self, tmp_path, bound, path, why):
        _project(tmp_path)
        _project(tmp_path, "altro", journal=False)
        (tmp_path / "memory").mkdir(exist_ok=True)
        tools = _store(tmp_path).build_tools()
        before = sorted(p for p in tmp_path.rglob("*") if p.is_file())

        out = await tools.get("write_file").execute(path=path, content="x")

        assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before, (
            f"{path} è stato scritto, e non doveva ({why}): {out}"
        )

    async def test_it_reads_inside_the_project_and_not_outside(self, tmp_path, bound):
        _project(tmp_path)
        _project(tmp_path, "altro", journal=False)
        read = _store(tmp_path).build_tools().get("read_file")

        mine = await read.execute(path="wikis/viaggio/raw/journal/20260822.md")
        theirs = await read.execute(path="wikis/altro/wiki/index.md")

        assert "gomme da cambiare" in mine
        assert "# viaggio" not in theirs


# ── Il prompt ────────────────────────────────────────────────────────────────


class TestThePrompt:
    def _prompt(self, tmp_path) -> str:
        _project(tmp_path)
        store = _store(tmp_path)
        return store.build_prompt(store.read_delta())

    def test_it_carries_the_journal_lines(self, tmp_path):
        prompt = self._prompt(tmp_path)
        assert "gomme da cambiare" in prompt
        assert "raw/journal/20260822.md" in prompt

    def test_it_names_the_only_writable_place(self, tmp_path):
        assert "`wikis/viaggio/wiki/`" in self._prompt(tmp_path)

    def test_it_never_hands_out_an_absolute_path(self, tmp_path):
        """Un assoluto nel prompt è un divieto travestito da istruzione: la
        allowlist tiene la forma *risolta*, e su Android le due divergono."""
        assert str(tmp_path) not in self._prompt(tmp_path)

    def test_the_map_is_fenced(self, tmp_path):
        """Le intestazioni di una pagina sbucherebbero nella struttura del
        prompt; e quel che sta in un file dell'utente è dato, non istruzione.
        Quattro backtick perché una pagina può contenere un blocco di codice."""
        assert "````markdown" in self._prompt(tmp_path)

    @pytest.mark.parametrize("rule", [
        "state: open",          # una riga promossa non nasce decisa
        "source:",              # la pista dalla pagina alla frase
        "Add and promote",      # non riscrivere
        "Never delete a page",
        "Follow the structure you find",
        "nothing to do",
    ])
    def test_the_rules_are_all_there(self, tmp_path, rule):
        assert rule in self._prompt(tmp_path)

    def test_the_map_exception_is_stated_where_the_rule_is(self, tmp_path):
        """La mappa è in parte derivata, quindi «non riscrivere» le va ritagliata
        addosso — e il ritaglio va scritto **dove sta la regola**, che è la
        lezione della posizione pagata tre volte in un giorno su questo ramo."""
        prompt = self._prompt(tmp_path)
        i_rule = prompt.index("Add and promote")
        i_carve = prompt.index('The map is the exception')
        assert i_carve > i_rule
        assert "amend" in prompt[i_carve:i_carve + 700]

    def test_an_empty_project_says_so_instead_of_listing_nothing(self, tmp_path):
        """Una rubrica che tace si legge come «non c'è niente da sapere», che è
        il silenzio che T3 ha già pagato una volta sull'inventario di Atlas."""
        _project(tmp_path)
        store = _store(tmp_path)
        assert "no pages yet" in store.build_prompt(store.read_delta())

    def test_the_pages_that_exist_are_listed(self, tmp_path):
        root = _project(tmp_path)
        (root / "wiki" / "furgone.md").write_text("# Furgone\n", "utf-8")
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta())

        assert "`furgone.md` — Furgone" in prompt
        assert "index.md`" not in prompt.split("## Pages that already exist")[1]

    def test_a_capped_delta_says_what_it_left(self, tmp_path):
        """Mai troncare zitti — e in più: il modello non deve mettersi a cercare
        le righe che mancano, quindi il prompt gli dice che arriveranno."""
        root = _project(tmp_path)
        (root / "raw" / "journal" / "20260823.md").write_text(
            "# 2026-08-23\n\n" + "".join(f"- 09:00 — fatto {i}\n" for i in range(20)), "utf-8"
        )
        store = GardenerStore(root, tmp_path, max_delta_lines=3)

        prompt = store.build_prompt(store.read_delta())

        assert "left for the next pass" in prompt
        assert "19 further journal lines" in prompt


# ── La passata ───────────────────────────────────────────────────────────────


class TestTheRun:
    async def test_no_new_lines_means_no_provider_call(self, tmp_path):
        _project(tmp_path, journal=False)
        agent = _FakeAgent(tmp_path)

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "skipped_no_delta"
        assert agent.calls == []
        assert not outcome.ran

    async def test_a_pass_that_wrote_advances_the_cursor_and_logs(self, tmp_path):
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=2))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "written"
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}
        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "gardener | 2 journal lines (20260822) → 2 writes" in log

    async def test_nothing_to_promote_is_an_outcome_and_leaves_no_log_line(self, tmp_path):
        """Il cursore avanza comunque — riproporre le stesse righe darebbe la
        stessa risposta a un costo nuovo — ma **il log resta pulito**: una riga
        per ogni giro a vuoto renderebbe illeggibile l'unico registro che c'è.
        """
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=0, ok=0))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "nothing_to_promote"
        assert read_state(root).cursor == {"raw/journal/20260822.md": 4}
        assert not (root / "log" / "20260823.md").exists()

    async def test_blocked_writes_leave_the_journal_unread(self, tmp_path):
        """La proprietà per cui il predicato di commit di Dream è condiviso: se
        il cursore avanzasse dopo una passata che ha provato a scrivere e non ci
        è riuscita, quelle righe risulterebbero digerite da un lavoro che non è
        avvenuto — e nessun giro successivo le rivedrebbe mai."""
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=2, ok=0))

        outcome = await run_gardener(_FakeAgent(tmp_path), store)

        assert outcome.status == "no_write"
        assert read_state(root).cursor == {}

    async def test_an_unfinished_turn_leaves_the_journal_unread(self, tmp_path):
        root = _project(tmp_path)
        store = _with_states(_store(tmp_path), _states(attempted=1, ok=1))

        outcome = await run_gardener(_FakeAgent(tmp_path, stop_reason="max_iterations"), store)

        assert outcome.status == "incomplete"
        assert read_state(root).cursor == {}

    async def test_a_dead_provider_is_an_outcome_not_a_crash(self, tmp_path):
        root = _project(tmp_path)

        outcome = await run_gardener(_ExplodingAgent(tmp_path), _store(tmp_path))

        assert outcome.status == "failed" and "provider is down" in outcome.detail
        assert read_state(root).cursor == {}

    async def test_the_second_pass_has_nothing_left_to_read(self, tmp_path):
        """L'idempotenza vista da fuori: una passata riuscita chiude il suo
        materiale, e la successiva non riparte da capo."""
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        outcome = await run_gardener(agent, _store(tmp_path))

        assert outcome.status == "skipped_no_delta"
        assert len(agent.calls) == 1

    async def test_each_pass_starts_from_a_fresh_session(self, tmp_path):
        """Il giardiniere non ha memoria dei propri giri: la sua memoria sono le
        pagine e il cursore. Una chiave con il timestamp lo rende vero invece di
        raccomandarlo."""
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        key = agent.calls[0]["session_key"]
        assert key.startswith("gardener:viaggio-")
        assert agent.calls[0]["ephemeral"] is True

    async def test_the_state_file_is_not_a_wiki_page(self, tmp_path):
        """Il cursore sta sotto ``.jenny/``: fuori dalle viste, fuori dal grafo,
        fuori dall'impronta di Atlas — e senza che nessuno dei tre lo impari."""
        root = _project(tmp_path)

        await run_gardener(_FakeAgent(tmp_path), _with_states(
            _store(tmp_path), _states(attempted=1, ok=1)
        ))

        assert (root / ".jenny" / "gardener.json").is_file()
        assert not list((root / "wiki").glob(".*"))


def test_the_state_survives_a_restart(tmp_path):
    """Il cursore è su disco, non in memoria: un riavvio non fa ricominciare."""
    root = _project(tmp_path)
    store = _store(tmp_path)
    store.commit(store.read_delta())

    assert read_state(root) != GardenerState()
    assert _store(tmp_path).read_delta().is_empty

# ── Il checkpoint ────────────────────────────────────────────────────────────


class TestTheCheckpoint:
    """Il giardiniere è il primo lavoro periodico che scrive dentro le cartelle
    *dell'utente*, non in un file derivato: Atlas ricostruisce ``memory/WIKI.md``
    al run dopo, una pagina scritta a mano e sovrascritta non si ricostruisce da
    niente — il diario copre solo quel che dal diario è nato."""

    async def test_a_pass_checkpoints_the_workspace_first(self, tmp_path):
        _project(tmp_path)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        assert agent.snapshots == ["pre_gardener"]

    async def test_a_tick_with_nothing_to_read_does_not_scan_the_workspace(self, tmp_path):
        """Uno snapshot per tick a vuoto sarebbe una scansione del workspace ogni
        mezz'ora per niente: il checkpoint sta **dopo** il cancello del delta."""
        _project(tmp_path, journal=False)
        agent = _FakeAgent(tmp_path)

        await run_gardener(agent, _store(tmp_path))

        assert agent.snapshots == []

    async def test_a_failing_checkpoint_does_not_stop_the_pass(self, tmp_path):
        """**Fail-open, e non è pigrizia.** Il verso opposto — «nessuna passata
        senza checkpoint» — trasformerebbe uno store di snapshot pieno in un
        taccuino che smette di lavorare in silenzio: un guasto peggiore di quello
        che previene. Il checkpoint è una rete, non un permesso."""
        _project(tmp_path)

        class _Broken(_FakeAgent):
            async def take_snapshot(self, trigger: str) -> bool:
                raise RuntimeError("snapshot store full")

        outcome = await run_gardener(
            _Broken(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert outcome.status == "written"

    async def test_no_hook_at_all_is_not_an_error(self, tmp_path):
        """Fuori dal gateway la rete non c'è (test, ispezione): la passata gira."""
        _project(tmp_path)

        class _Bare(_FakeAgent):
            take_snapshot = None

        outcome = await run_gardener(
            _Bare(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert outcome.status == "written"

    def test_the_model_is_not_told_it_is_protected(self, tmp_path):
        """Dream ha un ramo di prompt che promette la reversibilità, e serve a
        fargli potare di più. Qui non c'è e non ci va: aggiungere-e-promuovere
        vale *anche* con la rete, e prometterla sposterebbe il giudizio nella
        direzione sbagliata — verso il rimpasto."""
        _project(tmp_path)
        store = _store(tmp_path)

        prompt = store.build_prompt(store.read_delta()).lower()

        for promise in ("snapshot", "reversible", "checkpoint", "undo", "restore"):
            assert promise not in prompt, f"il prompt promette una rete: {promise}"

# ── Quel che la passata trova ────────────────────────────────────────────────


class TestTheFlag:
    """Il canale nasce da un buco: la risposta della passata serviva solo al
    predicato di commit e alla contabilità token, e il **testo** veniva buttato —
    quindi il prompt diceva «se due pagine si contraddicono, dillo» e quel report
    non arrivava a nessuno.

    Due destinazioni per due pubblici: la sezione aperta della **mappa**, che il
    modello scrive da sé e che entra nel prompt di ogni turno (quindi raggiunge la
    conversazione), e una riga nel **log**, che è la storia che una persona
    rilegge. Qui si prova la seconda.
    """

    @pytest.mark.parametrize("reply,expected", [
        ("ho fatto le pagine.\n\nNOTHING TO FLAG", None),
        ("fatto.\n\nFLAG: treno.md e tappe.md dicono due orari diversi",
         "treno.md e tappe.md dicono due orari diversi"),
        ("solo prosa senza formula", None),
        ("", None),
        ("FLAG:", None),
    ])
    def test_the_marker_is_read_and_prose_is_not(self, reply, expected):
        from jenny.agent.gardener import extract_flag

        assert extract_flag(SimpleNamespace(content=reply)) == expected

    def test_it_reads_the_marker_from_the_end(self):
        """Dal fondo, perché **il marcatore chiude** la risposta: cercandolo
        dall'inizio si prende la riga in cui il modello *cita* il contratto
        mentre ragiona, e si inventa una segnalazione che non c'è.

        La forma del test è quella che discrimina, e ci è voluta una mutazione
        per trovarla: la citazione deve stare a **inizio riga**, perché è così
        che un modello che ragiona sul proprio contratto scrive. Con la citazione
        in mezzo alla riga le due direzioni danno lo stesso risultato e il test
        non prova niente — era la prima stesura.
        """
        from jenny.agent.gardener import extract_flag

        quoting_then_clearing = (
            "FLAG: is for things I cannot settle on my own.\n"
            "Here the two pages agree, so nothing applies.\n"
            "NOTHING TO FLAG"
        )
        quoting_then_flagging = (
            "NOTHING TO FLAG would be wrong here: le pagine si contraddicono.\n"
            "FLAG: budget.md e tappe.md non concordano"
        )

        assert extract_flag(SimpleNamespace(content=quoting_then_clearing)) is None
        assert extract_flag(SimpleNamespace(content=quoting_then_flagging)) == (
            "budget.md e tappe.md non concordano"
        )

    def test_a_long_flag_is_cut_not_dropped(self):
        """Il log è «una riga per operazione»: un paragrafo lo rende illeggibile,
        ed è l'unico registro che c'è. Ma tagliare è meglio che perdere."""
        from jenny.agent.gardener import extract_flag

        flag = extract_flag(SimpleNamespace(content="FLAG: " + "x" * 500))

        assert flag is not None and len(flag) == 300

    def test_a_reply_with_no_content_is_not_an_error(self):
        from jenny.agent.gardener import extract_flag

        assert extract_flag(None) is None
        assert extract_flag(SimpleNamespace(metadata={})) is None

    async def test_a_flag_reaches_the_log(self, tmp_path):
        root = _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        agent.reply = "fatto.\n\nFLAG: due pagine dicono orari diversi"

        await run_gardener(agent, _with_states(_store(tmp_path), _states(attempted=1, ok=1)))

        log = (root / "log" / "20260823.md").read_text(encoding="utf-8")
        assert "- flagged: due pagine dicono orari diversi" in log

    async def test_no_flag_leaves_the_log_line_alone(self, tmp_path):
        root = _project(tmp_path)

        await run_gardener(
            _FakeAgent(tmp_path), _with_states(_store(tmp_path), _states(attempted=1, ok=1))
        )

        assert "flagged" not in (root / "log" / "20260823.md").read_text(encoding="utf-8")

    async def test_a_flag_is_kept_even_when_the_pass_promoted_nothing(self, tmp_path):
        """Una passata a vuoto non lascia traccia — era la regola e resta — ma una
        segnalazione è la cosa più importante che una passata possa dire, e non si
        perde per non aver promosso niente."""
        root = _project(tmp_path)
        agent = _FakeAgent(tmp_path)
        agent.reply = "niente da promuovere.\n\nFLAG: la pagina treno cita una fonte assente"

        outcome = await run_gardener(
            agent, _with_states(_store(tmp_path), _states(attempted=0, ok=0))
        )

        assert outcome.status == "nothing_to_promote"
        assert "la pagina treno cita una fonte assente" in (
            root / "log" / "20260823.md"
        ).read_text(encoding="utf-8")


class TestTheClosingContract:
    def _prompt(self, tmp_path) -> str:
        _project(tmp_path)
        store = _store(tmp_path)
        return store.build_prompt(store.read_delta())

    @pytest.mark.parametrize("piece", ["NOTHING TO FLAG", "FLAG:"])
    def test_the_two_closing_lines_are_specified(self, tmp_path, piece):
        assert piece in self._prompt(tmp_path)

    def test_a_contradiction_goes_into_the_map_not_into_prose(self, tmp_path):
        """La destinazione scartata era ``audit/``: vuole ``anchor_before`` e
        ``target_lines``, cioè è ancorata a un intervallo di righe perché nasce
        per correggere un testo. «Queste due pagine si contraddicono» non ha
        un'ancora, e inventarne una vuol dire scrivere un'ancora finta in un
        canale che il lint verifica."""
        prompt = self._prompt(tmp_path)

        assert "write the question" in prompt and "open section" in prompt
        assert "say so in your reply" not in prompt

    def test_deciding_it_alone_is_the_forbidden_thing(self, tmp_path):
        assert "Deciding it yourself is the one thing you must not do" in self._prompt(tmp_path)

# ── Il controllo incrociato ──────────────────────────────────────────────────


def _transcript_path(name: str) -> Path:
    from jenny.config.paths import get_webui_dir

    return get_webui_dir() / f"websocket_project_{name}.jsonl"


@pytest.fixture(autouse=True)
def _no_leftover_transcript():
    """La dir della WebUI è **condivisa** dalla fixture di workspace, che è di
    sessione: un transcript scritto da un test resta lì per il successivo. Visto
    subito (un test che verificava l'assenza della sezione la trovava), e vale la
    pena tenerlo pulito qui invece che ricordarselo in ogni test."""
    yield
    for name in ("orto", "viaggio"):
        _transcript_path(name).unlink(missing_ok=True)


def _transcript(tmp_path: Path, name: str, *messages: str) -> Path:
    """Il transcript di un progetto, nella forma che scrive la WebUI."""
    path = _transcript_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, message in enumerate(messages):
        rows.append(json.dumps({
            "event": "user", "chat_id": f"project:{name}", "text": message,
            "turn_id": f"t{i}", "turn_phase": "user", "turn_seq": i,
        }))
        # Il ragionamento **nomina l'utente**, ed è la forma vera: sul telefono
        # una riga di reasoning dice letteralmente «The user is telling me facts
        # about…». Un testo di prova che non contiene la parola veniva scartato
        # dal filtro grezzo, quindi il filtro vero risultava non provato —
        # trovato per mutazione, ed è il terzo dato di prova irrealistico di
        # oggi.
        # Due righe di rumore, e la seconda è quella che conta. Il codice ha un
        # pre-filtro grezzo (``'"user"' not in raw``) che scarta la gran parte dei
        # delta senza parsarli, e un controllo vero su ``event``/``role``. Una
        # riga di ragionamento normale è scartata dal pre-filtro, quindi da sola
        # **non prova** il controllo vero: ci vuole una riga che il pre-filtro
        # ammette — e un modello che ragiona scrive davvero `the "user" wants`,
        # col termine fra virgolette. Trovato per mutazione, due volte di fila.
        rows.append(json.dumps({
            "event": "reasoning_delta", "chat_id": f"project:{name}",
            "text": "The user is telling me facts; pensiero che non deve entrare nel confronto",
        }))
        rows.append(json.dumps({
            "event": "reasoning_delta", "chat_id": f"project:{name}",
            "text": 'quoting the "user" verbatim: ragionamento che non è una cosa detta',
        }))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


class TestReadingWhatWasSaid:
    """Il lato «detto» del controllo incrociato.

    Nessun cursore sul transcript, ed è una decisione: le righe non portano un
    timestamp e il file attivo **ruota** in segmenti, quindi un conteggio di righe
    si azzererebbe senza dirlo. Le ultime N invece sono sempre leggibili, e lo
    stato del confronto è **il diario stesso** — quel che è stato recuperato ci
    sta dentro, quindi il giro dopo non si recupera due volte.
    """

    def test_it_reads_only_the_user_turns(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", "primo messaggio", "secondo messaggio")

        said, truncated = read_recent_user_messages("orto")

        assert said == ["primo messaggio", "secondo messaggio"]
        assert truncated is False

    def test_a_project_that_never_talked_gives_nothing(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        assert read_recent_user_messages("mai-parlato") == ([], False)

    def test_it_keeps_the_tail_and_says_it_cut(self, tmp_path):
        """Taglia **dalla testa**: i messaggi più recenti sono quelli che la
        cattura può aver mancato adesso."""
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", *[f"messaggio {i}" for i in range(10)])

        said, truncated = read_recent_user_messages("orto", limit=3)

        assert said == ["messaggio 7", "messaggio 8", "messaggio 9"]
        assert truncated is True

    def test_the_char_ceiling_also_cuts_from_the_head(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages

        _transcript(tmp_path, "orto", "x" * 100, "y" * 100, "z" * 100)

        said, truncated = read_recent_user_messages("orto", max_chars=150)

        assert said == ["z" * 100]
        assert truncated is True

    def test_a_broken_line_does_not_stop_the_rest(self, tmp_path):
        from jenny.agent.gardener import read_recent_user_messages
        from jenny.config.paths import get_webui_dir

        path = _transcript(tmp_path, "orto", "buono")
        with path.open("a", encoding="utf-8") as fh:
            fh.write('{"event":"user","text":"rotto\n')
        assert get_webui_dir().is_dir()

        said, _ = read_recent_user_messages("orto")

        assert said == ["buono"]


class TestTheCrossCheck:
    def _prompt(self, tmp_path, *messages: str):
        _project(tmp_path, "orto")
        if messages:
            _transcript(tmp_path, "orto", *messages)
        store = _store(tmp_path, "orto")
        return store.build_prompt(store.read_delta())

    def test_what_was_said_reaches_the_prompt(self, tmp_path):
        prompt = self._prompt(tmp_path, "il furgone ha le gomme da cambiare")

        assert "What the user actually said" in prompt
        assert "gomme da cambiare" in prompt

    def test_the_journal_of_those_days_comes_with_it(self, tmp_path):
        """Il delta contiene solo le righe **nuove**, e un fatto già catturato sta
        spesso *sotto* il cursore: confrontare il detto col solo delta
        segnalerebbe come mancante tutto quel che una passata precedente aveva già
        letto — cioè, sulla seconda passata di una giornata, quasi tutto."""
        prompt = self._prompt(tmp_path, "una cosa detta")

        assert "What the journal recorded on those days, in full" in prompt

    def test_without_a_transcript_the_section_is_absent(self, tmp_path):
        """Nessun transcript vuol dire nessun confronto: una sezione vuota
        chiederebbe al modello di cercare in un posto che non c'è."""
        prompt = self._prompt(tmp_path)

        assert "What the user actually said" not in prompt

    def test_the_thinking_of_past_turns_is_not_offered_as_something_said(self, tmp_path):
        """Un transcript è fatto in gran parte di ragionamento e delta. Prenderli
        per «cose dette dall'utente» farebbe recuperare nel diario i pensieri del
        modello — cioè trasformerebbe l'anticorpo nella malattia."""
        prompt = self._prompt(tmp_path, "cosa vera")

        assert "pensiero che non deve entrare" not in prompt
        assert "ragionamento che non è una cosa detta" not in prompt

    @pytest.mark.parametrize("rule", [
        "a stable fact they said that the journal never recorded",
        "When in doubt, leave it",
        "still be true next week",
        "cannot change the journal, only add to it",
    ])
    def test_the_task_and_its_three_limits_are_stated(self, tmp_path, rule):
        assert rule in self._prompt(tmp_path, "una cosa")


class TestRecovering:
    async def test_a_recovered_line_is_marked_and_appended(self, tmp_path):
        """Il marcatore sta nel **codice** e non nel prompt, perché è l'unico modo
        che ha di non essere dimenticato: una riga di diario senza origine è
        indistinguibile da una detta a voce quel giorno."""
        from jenny.agent.gardener import RECOVERED_MARKER

        root = _project(tmp_path, "orto")
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            out = await tool.execute(text="il vicino si chiama Enzo")
        finally:
            reset_workspace_scope(token)

        from datetime import date

        # Il recupero va nella pagina di **oggi**, non nel giorno che stava
        # confrontando: quella riga la scrive adesso, e datarla ieri la
        # nasconderebbe sotto il cursore di una passata già fatta.
        today = root / "raw" / "journal" / f"{date.today():%Y%m%d}.md"
        yesterday = root / "raw" / "journal" / "20260822.md"

        text = today.read_text(encoding="utf-8")
        assert RECOVERED_MARKER in text
        assert "il vicino si chiama Enzo" in text
        assert "journal" in out
        assert RECOVERED_MARKER not in yesterday.read_text(encoding="utf-8")

    async def test_it_appends_and_leaves_the_earlier_lines_alone(self, tmp_path):
        """La proprietà per cui questo tool può entrare in una cassetta chiusa:
        appende, e non c'è modo di riscrivere la fonte da cui sta promuovendo."""
        root = _project(tmp_path, "orto")
        page = next(iter((root / "raw" / "journal").glob("*.md")))
        before = page.read_bytes()
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            await tool.execute(text="recuperato")
        finally:
            reset_workspace_scope(token)

        assert page.read_bytes().startswith(before)

    async def test_the_project_is_injected_not_deduced(self, tmp_path):
        """Su un turno dell'utente il progetto si deduce dallo scope, ed è giusto.
        Ma una passata interna gira con lo scope **di default**, quindi la
        deduzione darebbe «nessun progetto» e il tool rifiuterebbe: chi costruisce
        la cassetta sa su quale progetto sta lavorando e lo passa."""
        root = _project(tmp_path, "orto")
        token = bind_workspace_scope(default_workspace_scope(tmp_path, True))
        try:
            tool = _store(tmp_path, "orto").build_tools().get("journal_append")
            out = await tool.execute(text="un fatto")
        finally:
            reset_workspace_scope(token)

        assert "No journal here" not in out
        assert list((root / "raw" / "journal").glob("*.md"))
