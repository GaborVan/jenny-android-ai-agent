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

    async def process_direct(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return SimpleNamespace(metadata={"_stop_reason": self._stop_reason}, usage={})

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
        """
        _project(tmp_path)
        tools = _store(tmp_path).build_tools()

        assert set(tools.tool_names) == {
            "read_file", "list_dir", "find_files", "grep",
            "write_file", "edit_file", "apply_patch",
        }

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
