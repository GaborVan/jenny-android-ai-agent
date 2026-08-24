"""Lo slash command ``/dream budget``.

I budget della memoria lunga nascono a 0 ("misurato ma non applicato") e i numeri
veri vanno scelti dalle misure del device. Questo comando è l'unico modo di
leggerle e di scrivere i tetti senza una shell di root sul telefono, quindi qui
si verifica che legga davvero il disco, che scriva davvero la config, e — la
parte che conta di più — che il ramo *senza* argomento sia rimasto quello di
prima.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.agent import dream_cycle
from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
from jenny.command import builtin
from jenny.command.builtin import register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter
from jenny.config.loader import load_config, save_config
from jenny.config.schema import Config
from jenny.utils.helpers import sync_workspace_templates

# MEMORY.md deve essere abbastanza grande da poter finire *sopra* un budget
# plausibile: è lo stato reale sul device ed è il caso che la conferma deve
# segnalare.
_MEMORY_TEXT = "# Memory\n" + "".join(f"- fact number {i}\n" for i in range(40))
_USER_TEXT = "# User\n- Name: Ludovico\n"
_SOUL_TEXT = "# Soul\n- Helpful, concise.\n"


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace vero con i template estratti e un `config.json` di default.

    ``render_template`` legge da ``get_workspace_path()``, non dal package: il
    ramo senza argomento non riuscirebbe nemmeno a costruire il prompt di Dream
    senza i template su disco. L'ambiente Jinja è memoizzato per processo, quindi
    la cache va invalidata o la prima chiamata della suite fissa la root per
    tutte le altre.
    """
    from jenny.runtime.context import get_runtime_context
    from jenny.utils import prompt_templates

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    sync_workspace_templates(ws, silent=True)

    ctx = get_runtime_context()
    monkeypatch.setattr(ctx, "workspace_dir", ws)
    monkeypatch.setattr(ctx, "config_path", ws / "config.json")
    prompt_templates._environment.cache_clear()
    save_config(Config(), ws / "config.json")
    yield ws
    prompt_templates._environment.cache_clear()


@pytest.fixture()
def memory(workspace: Path) -> MemoryStore:
    store = MemoryStore(workspace)
    store.memory_file.write_text(_MEMORY_TEXT, encoding="utf-8")
    store.user_file.write_text(_USER_TEXT, encoding="utf-8")
    store.soul_file.write_text(_SOUL_TEXT, encoding="utf-8")
    return store


@pytest.fixture()
def published() -> list:
    return []


@pytest.fixture()
def loop(workspace: Path, memory: MemoryStore, published: list) -> SimpleNamespace:
    """Il minimo di ``AgentLoop`` che ``cmd_dream`` tocca, su entrambi i rami."""
    calls: list[str] = []

    async def _publish(message):
        published.append(message)

    async def _process_direct(prompt, **_kwargs):
        calls.append(prompt)
        # ``_stop_reason: completed`` è ciò che ``internal_run_completed``
        # guarda; senza, il ramo senza argomento riporterebbe un turno mozzato.
        return SimpleNamespace(content="done", metadata={"_stop_reason": "completed"})

    return SimpleNamespace(
        bus=SimpleNamespace(publish_outbound=_publish),
        context=SimpleNamespace(memory=memory, timezone=None),
        sessions=SimpleNamespace(sessions_dir=workspace / "sessions"),
        process_direct=_process_direct,
        evict_pruned_sessions=lambda _keys: None,
        prompts=calls,
    )


def _ctx(loop: SimpleNamespace, raw: str) -> CommandContext:
    msg = InboundMessage(
        channel="websocket", sender_id="u", chat_id="default", content=raw
    )
    return CommandContext(msg=msg, session=None, key="k", raw=raw, loop=loop)


async def _drain() -> None:
    """Lascia girare il task fire-and-forget del ramo senza argomento."""
    for _ in range(50):
        await asyncio.sleep(0)


def _config_path(workspace: Path) -> Path:
    return workspace / "config.json"


def _dream_config(workspace: Path):
    return load_config(_config_path(workspace)).agents.defaults.dream


class TestRegistration:
    def test_is_dispatchable_with_and_without_args(self, router):
        assert router.is_dispatchable_command("/dream")
        assert router.is_dispatchable_command("/dream budget")
        assert router.is_dispatchable_command("/dream budget memory 6000")

    def test_help_advertises_the_budget_argument(self):
        from jenny.command.builtin import BUILTIN_COMMAND_SPECS

        spec = {s.command: s for s in BUILTIN_COMMAND_SPECS}["/dream"]

        assert spec.arg_hint
        assert "budget" in spec.description.lower()


class TestPlainDreamIsUntouched:
    """Regressione — il test più importante del file.

    Aggiungere un argomento a `/dream` significa infilare un ramo davanti a un
    percorso che esisteva già e che funziona. Se `/dream` nudo smettesse di
    consolidare, o cominciasse a rispondere con dei budget, la feature avrebbe
    rotto la sola cosa che il comando faceva prima.
    """

    @pytest.mark.asyncio
    async def test_bare_dream_still_runs_the_consolidation(
        self, router, loop, memory, published
    ):
        memory.append_history("user: ricordati che il gateway gira su Android")

        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        assert ack.content == "Dreaming..."
        # Il consolidamento è partito davvero: il prompt di Dream è arrivato al
        # provider, non solo un messaggio di stato in chat.
        assert loop.prompts and "Conversation History" in loop.prompts[0]
        assert published and "Dream completed" in published[0].content

    @pytest.mark.asyncio
    async def test_bare_dream_prints_no_budget(self, router, loop, memory, published):
        memory.append_history("user: qualcosa da consolidare")

        ack = await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        everything = ack.content + "".join(m.content for m in published)
        assert "budget" not in everything.lower()

    @pytest.mark.asyncio
    async def test_bare_dream_does_not_write_config(self, router, loop, memory, workspace):
        memory.append_history("user: qualcosa da consolidare")
        before = _config_path(workspace).stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream"))
        await _drain()

        assert _config_path(workspace).stat().st_mtime_ns == before


class TestShowReport:
    @pytest.mark.asyncio
    async def test_lists_the_three_files_with_their_sizes(self, router, loop, memory):
        out = await router.dispatch(_ctx(loop, "/dream budget"))

        for label, text in (
            ("MEMORY.md", _MEMORY_TEXT),
            ("USER.md", _USER_TEXT),
            ("SOUL.md", _SOUL_TEXT),
        ):
            assert label in out.content
            assert f"{len(text):,}" in out.content

    @pytest.mark.asyncio
    async def test_a_zero_budget_file_is_shown_as_measured_not_enforced(
        self, router, loop
    ):
        """Lo stato di default di tutti e tre, e il numero che si viene a leggere.

        Ometterlo perché "non ha un budget" nasconderebbe esattamente la riga per
        cui il comando esiste.
        """
        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "no budget" in out.content
        assert "measured, not enforced" in out.content

    @pytest.mark.asyncio
    async def test_shows_the_review_cadence_and_state(self, router, loop, memory):
        memory.set_review_state(runs_since_review=7, stuck_runs=2)

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "every 12 Dream runs" in out.content
        assert "7 runs since the last one" in out.content
        assert "2 stuck runs" in out.content

    @pytest.mark.asyncio
    async def test_an_enforced_file_shows_its_percentage(
        self, router, loop, memory, workspace
    ):
        await router.dispatch(_ctx(loop, "/dream budget memory 400"))

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        pct = len(_MEMORY_TEXT) * 100 // 400
        assert f"/ 400 chars ({pct}%)" in out.content
        assert f"over budget by {len(_MEMORY_TEXT) - 400:,}" in out.content

    @pytest.mark.asyncio
    async def test_it_says_whose_writes_the_caps_bind(self, router, loop, workspace):
        """I tetti valgono per Dream, non per l'agente principale.

        È la vista in cui si scelgono i numeri, quindi è dove va detto su chi
        cadono. Senza, un file portato al cap da una conversazione resta un fatto
        inspiegato proprio nel posto in cui si viene a spiegarselo — e la
        catena misurata sul Titan 2 è esattamente quella: un turno di chat satura
        il file, poi è Dream a non trovare spazio.
        """
        await router.dispatch(_ctx(loop, "/dream budget user 400"))

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "Enforced on Dream's own writes only" in out.content
        assert "A chat turn is never refused" in out.content

    @pytest.mark.asyncio
    async def test_with_nothing_enforced_it_does_not_say_it(self, router, loop, workspace):
        """Con tutti e tre i budget a `0` non c'è nessun vincolo di cui dire a chi
        si applica, e la riga sarebbe solo rumore."""
        for name in ("memory", "user", "soul"):
            await router.dispatch(_ctx(loop, f"/dream budget {name} 0"))

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "no budget" in out.content
        assert "Enforced on Dream" not in out.content

    @pytest.mark.asyncio
    async def test_reading_does_not_rewrite_config(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream budget"))

        assert _config_path(workspace).stat().st_mtime_ns == before


class TestTheBlockedDiagnosis:
    """Questa è la vista in cui si atterra dopo l'alert di sistema.

    L'alert dice "Dream è fermo, vieni a vedere le misure" e non porta cifre:
    da ``finish_dream_cycle`` non ce ne sono. Quindi la diagnosi per esteso —
    quale file sta bloccando, di quanto sfora, quale comando lo sblocca — deve
    stare qui, e un numero in coda alla riga del review non è una diagnosi.
    """

    @pytest.mark.asyncio
    async def test_below_the_threshold_it_stays_a_number(self, router, loop, memory):
        memory.set_review_state(
            runs_since_review=3, stuck_runs=dream_cycle.STUCK_IS_ALARMING - 1
        )

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "Dream is blocked" not in out.content

    @pytest.mark.asyncio
    async def test_it_names_the_file_and_the_way_out(
        self, router, loop, memory, workspace
    ):
        await router.dispatch(_ctx(loop, "/dream budget memory 400"))
        memory.set_review_state(
            runs_since_review=3, stuck_runs=dream_cycle.STUCK_IS_ALARMING
        )

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "**Dream is blocked.**" in out.content
        # La stessa frase dell'alert, da un'unica stesura: se divergessero, la
        # seconda smentirebbe la prima nel momento peggiore.
        assert dream_cycle.format_stuck_alarm(dream_cycle.STUCK_IS_ALARMING) in out.content
        assert "`MEMORY.md`" in out.content
        assert "set it to `0`" in out.content

    @pytest.mark.asyncio
    async def test_with_no_file_over_budget_it_does_not_promise_a_cap_to_raise(
        self, router, loop, memory
    ):
        """Bloccato ma nessun file sopra soglia: il rifiuto viene da altro.

        Dire "alza il budget" qui manderebbe a cercare una leva che non c'entra;
        i budget di default stanno larghi sui file di questo workspace.
        """
        memory.set_review_state(
            runs_since_review=3, stuck_runs=dream_cycle.STUCK_IS_ALARMING
        )

        out = await router.dispatch(_ctx(loop, "/dream budget"))

        assert "**Dream is blocked.**" in out.content
        assert "No file is over budget" in out.content
        assert "Raise the budget" not in out.content


class TestWrite:
    @pytest.mark.asyncio
    async def test_a_budget_reaches_the_file_on_disk(self, router, loop, workspace):
        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        assert _dream_config(workspace).memory_budget_chars == 6000
        # Camel case: è la forma con cui il device legge la config.
        raw = json.loads(_config_path(workspace).read_text(encoding="utf-8"))
        assert raw["agents"]["defaults"]["dream"]["memoryBudgetChars"] == 6000
        # Il "prima" è il default di spedizione, non zero: scritto come `0 → …`
        # questa assert passava lo stesso, per sottostringa di `3,000 → …`.
        assert "3,000 → 6,000 chars" in out.content

    @pytest.mark.asyncio
    async def test_each_name_writes_its_own_field(self, router, loop, workspace):
        await router.dispatch(_ctx(loop, "/dream budget user 1500"))
        await router.dispatch(_ctx(loop, "/dream budget soul 900"))
        # 24 e non 4: sotto ``_REVIEW_CADENCE_FLOOR`` il comando rifiuta, e questo
        # test misura il routing del nome sul campo, non il pavimento.
        await router.dispatch(_ctx(loop, "/dream budget review 24"))

        dream = _dream_config(workspace)
        assert (dream.user_budget_chars, dream.soul_budget_chars) == (1500, 900)
        assert dream.review_every_runs == 24
        # Non nominato, quindi fermo al default di spedizione.
        assert dream.memory_budget_chars == 3000

    @pytest.mark.asyncio
    async def test_review_confirmation_speaks_in_runs(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget review 24"))

        assert "every 12 → every 24 runs" in out.content

    @pytest.mark.asyncio
    async def test_setting_zero_says_enforcement_is_off(self, router, loop, workspace):
        await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        out = await router.dispatch(_ctx(loop, "/dream budget memory 0"))

        assert _dream_config(workspace).memory_budget_chars == 0
        assert "Enforcement is off" in out.content
        assert "Nothing on disk changed." in out.content

    @pytest.mark.asyncio
    async def test_setting_the_same_value_does_not_touch_the_file(
        self, router, loop, workspace, monkeypatch
    ):
        """Ribattere lo stesso numero non deve ruotare il `.bak` per niente.

        La prova diretta è che ``apply`` ritorni ``False``, cioè che
        ``store.mutate`` non arrivi mai a ``save_config``; l'mtime è la conferma
        sul disco.
        """
        import jenny.config.store as store_module

        await router.dispatch(_ctx(loop, "/dream budget memory 6000"))
        before = _config_path(workspace).stat().st_mtime_ns

        saves: list[int] = []
        monkeypatch.setattr(
            store_module, "save_config", lambda *a, **k: saves.append(1)
        )
        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        assert saves == []
        assert _config_path(workspace).stat().st_mtime_ns == before
        assert "already set to 6,000 chars" in out.content
        assert "was not rewritten" in out.content

    @pytest.mark.asyncio
    async def test_warns_when_the_file_is_already_over_the_new_budget(
        self, router, loop
    ):
        out = await router.dispatch(_ctx(loop, "/dream budget memory 100"))

        assert f"the file is {len(_MEMORY_TEXT):,} chars today" in out.content
        assert f"already {len(_MEMORY_TEXT) - 100:,} over the new budget" in out.content
        assert "Nothing was deleted" in out.content

    @pytest.mark.asyncio
    async def test_no_warning_when_the_file_fits(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget memory 99999"))

        assert "over the new budget" not in out.content

    @pytest.mark.asyncio
    async def test_review_already_set_reads_as_a_sentence(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget review 12"))

        assert out.content.startswith("Dream review pass is already set to 12 runs")

    @pytest.mark.asyncio
    async def test_a_confirmation_does_not_repeat_the_usage_block(self, router, loop):
        """Le forme valide servono a chi legge o ha sbagliato, non a chi ha appena scritto."""
        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        assert "Valid forms:" not in out.content


class TestWritePath:
    @pytest.mark.asyncio
    async def test_the_write_goes_through_store_mutate_and_not_save_config(
        self, router, loop, workspace, monkeypatch
    ):
        """La regola di progetto, resa un test.

        ``save_config`` riscrive il file intero da una copia che il chiamante ha
        letto prima, quindi cancella in silenzio quello che un altro scrittore ha
        appena cambiato; ``mutate`` rilegge *dentro* il lock in cui scrive. Il
        modo realistico di sbagliare è un `from jenny.config.loader import
        save_config` locale dentro il comando — che risolve l'attributo alla
        chiamata, e quindi finisce dritto in questa trappola.
        """
        import jenny.config.loader as loader_module
        import jenny.config.store as store_module

        def _forbidden(*_args, **_kwargs):
            raise AssertionError("save_config called from the command layer")

        monkeypatch.setattr(loader_module, "save_config", _forbidden)

        seen: list[bool] = []
        real_mutate = store_module.mutate

        async def _tracking_mutate(apply, **kwargs):
            seen.append(True)
            return await real_mutate(apply, **kwargs)

        monkeypatch.setattr(store_module, "mutate", _tracking_mutate)

        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        assert seen == [True]
        # ``store.mutate`` usa il ``save_config`` legato al *suo* namespace
        # all'import, che il monkeypatch qui sopra non tocca: la scrittura è
        # avvenuta davvero, ed è passata solo di lì.
        assert _dream_config(workspace).memory_budget_chars == 6000
        assert "6,000 chars" in out.content


class TestValidation:
    @pytest.mark.asyncio
    async def test_a_negative_budget_is_refused(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget memory -1"))

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert _dream_config(workspace).memory_budget_chars == 3000
        assert "cannot be negative" in out.content
        assert "`0`" in out.content

    @pytest.mark.asyncio
    async def test_a_non_numeric_value_is_refused(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget memory sei-mila"))

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert "not a whole number" in out.content
        assert "/dream budget memory <chars>" in out.content

    @pytest.mark.asyncio
    async def test_review_zero_is_refused(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget review 0"))

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert _dream_config(workspace).review_every_runs == 12
        assert "at least 1 run" in out.content


class TestReviewCadenceFloor:
    """Il pavimento della cadenza di review è applicato, non stampato.

    ``reviewEveryRuns`` sotto soglia fa incontrare due passate di review sullo
    stesso file: la seconda arriva su un file già potato e continua a cercare cose
    da togliere (misurato — ``USER.md`` 3.524 → 1.626, il 31% sulla sola seconda
    passata, e una passata forzata che portò via cinque voci reali). Lo schema
    resta ``ge=1`` perché un restore deve poter riscrivere qualunque valore
    storico, quindi l'unico posto in cui il numero è difeso è questo comando.
    """

    @pytest.mark.asyncio
    async def test_below_the_floor_is_refused_and_names_the_floor(
        self, router, loop, workspace
    ):
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget review 4"))

        # Il file non è stato toccato: né riscritto, né il `.bak` ruotato.
        assert _config_path(workspace).stat().st_mtime_ns == before
        assert _dream_config(workspace).review_every_runs == 12
        assert f"below the floor of {builtin._REVIEW_CADENCE_FLOOR}" in out.content
        assert "was not written" in out.content

    @pytest.mark.asyncio
    async def test_one_is_refused_too(self, router, loop, workspace):
        """``review 1`` è la configurazione misurata come distruttiva, non un caso limite."""
        out = await router.dispatch(_ctx(loop, "/dream budget review 1"))

        assert _dream_config(workspace).review_every_runs == 12
        assert "below the floor" in out.content

    def test_the_floor_is_twelve_and_equals_the_shipped_default(self):
        """Il numero, fissato: dodici, e non il sei che era solo stampato.

        Sei era la soglia dell'avviso e nasceva dalla cancellazione terminale,
        che la fase 2 del piano ha chiuso (``make_entry_archiver``). Il numero che
        resta è quello che il piano tiene — *"keep ``reviewEveryRuns`` at 12 and
        treat forced reviews as the rare path they are meant to be"* — e scendere
        sotto è l'item 6.1, deliberatamente non fatto. Pavimento e default devono
        restare lo stesso numero: un pavimento sotto il default sarebbe una zona
        in cui il comando scrive un valore che il piano non vuole.
        """
        from jenny.config.schema import DreamConfig

        assert builtin._REVIEW_CADENCE_FLOOR == 12
        assert DreamConfig().review_every_runs == builtin._REVIEW_CADENCE_FLOOR

    @pytest.mark.asyncio
    async def test_six_the_old_advised_floor_is_now_refused(
        self, router, loop, workspace
    ):
        """Il cambiamento di comportamento di questo task, in una riga."""
        out = await router.dispatch(_ctx(loop, "/dream budget review 6"))

        assert _dream_config(workspace).review_every_runs == 12
        assert "below the floor" in out.content

    @pytest.mark.asyncio
    async def test_the_refusal_prints_the_measurement(self, router, loop):
        """Il numero deve arrivare con la misura che lo giustifica, non da solo."""
        out = await router.dispatch(_ctx(loop, "/dream budget review 4"))

        assert "3,524 to 1,626" in out.content
        assert "five real entries" in out.content

    @pytest.mark.asyncio
    async def test_the_refusal_offers_the_confirmation_phrase_for_this_value(
        self, router, loop
    ):
        out = await router.dispatch(_ctx(loop, "/dream budget review 3"))

        assert (
            f"/dream budget review 3 {builtin._REVIEW_CADENCE_OVERRIDE}" in out.content
        )

    @pytest.mark.asyncio
    async def test_exactly_the_floor_is_accepted(self, router, loop, workspace):
        await router.dispatch(_ctx(loop, "/dream budget review 24"))

        out = await router.dispatch(
            _ctx(loop, f"/dream budget review {builtin._REVIEW_CADENCE_FLOOR}")
        )

        assert _dream_config(workspace).review_every_runs == 12
        assert "every 24 → every 12 runs" in out.content
        assert "below the floor" not in out.content

    @pytest.mark.asyncio
    async def test_the_confirmation_phrase_writes_the_value(
        self, router, loop, workspace
    ):
        out = await router.dispatch(
            _ctx(loop, f"/dream budget review 1 {builtin._REVIEW_CADENCE_OVERRIDE}")
        )

        assert _dream_config(workspace).review_every_runs == 1
        raw = json.loads(_config_path(workspace).read_text(encoding="utf-8"))
        assert raw["agents"]["defaults"]["dream"]["reviewEveryRuns"] == 1
        assert "every 12 → every 1 runs" in out.content
        # Scritto, ma non in silenzio: la conferma dice cosa resta acceso.
        assert "back-to-back" in out.content
        assert "`memory/archive/`" in out.content

    @pytest.mark.asyncio
    async def test_a_wrong_third_token_is_refused_not_swallowed(
        self, router, loop, workspace
    ):
        """Chi ha scritto un terzo token voleva confermare: mangiarselo scriverebbe a sua insaputa."""
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget review 1 --force"))

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert _dream_config(workspace).review_every_runs == 12
        assert "`--force` is not the confirmation phrase" in out.content
        assert builtin._REVIEW_CADENCE_OVERRIDE in out.content

    @pytest.mark.asyncio
    async def test_the_phrase_is_not_a_flag_a_model_would_guess(self):
        """Non un ``--force``: una frase in prima persona, con dei trattini e non un flag."""
        phrase = builtin._REVIEW_CADENCE_OVERRIDE
        assert not phrase.startswith("-")
        assert phrase.count("-") >= 3
        assert phrase not in {"force", "yes", "confirm", "unsafe"}

    @pytest.mark.asyncio
    async def test_the_phrase_is_pointless_above_the_floor(
        self, router, loop, workspace
    ):
        """Sopra soglia la frase non serve, ma non deve nemmeno rompere niente."""
        out = await router.dispatch(
            _ctx(loop, f"/dream budget review 24 {builtin._REVIEW_CADENCE_OVERRIDE}")
        )

        assert _dream_config(workspace).review_every_runs == 24
        assert "every 12 → every 24 runs" in out.content

    @pytest.mark.asyncio
    async def test_a_third_token_is_refused_for_the_size_budgets(
        self, router, loop, workspace
    ):
        """La conferma è solo di ``review``: sui tetti di dimensione non esiste."""
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(
            _ctx(loop, f"/dream budget memory 100 {builtin._REVIEW_CADENCE_OVERRIDE}")
        )

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert _dream_config(workspace).memory_budget_chars == 3000
        assert "at most a name and a value" in out.content

    @pytest.mark.asyncio
    async def test_the_usage_block_names_the_floor_not_the_schema_minimum(self):
        """Chi legge le forme valide deve leggere il numero che il comando applica."""
        usage = builtin._dream_usage()

        assert f"minimum {builtin._REVIEW_CADENCE_FLOOR})" in usage
        # `minimum 1` nudo è sottostringa di `minimum 12`: la parentesi di chiusura
        # è ciò che distingue il vecchio minimo dello schema dal nuovo pavimento.
        assert "minimum 1)" not in usage

    def test_the_schema_still_accepts_a_restored_value_below_the_floor(self):
        """Il pavimento vive nel comando *perché* lo schema non deve alzarlo.

        Un ``ge=12`` renderebbe illeggibile un `config.json` con
        ``reviewEveryRuns: 1``; ``loader._load_with_recovery`` proverebbe il
        `.bak` — stesso valore — e poi metterebbe il file in quarantena
        ripartendo dai default, provider e chiave API inclusi.
        """
        from jenny.config.schema import DreamConfig

        assert DreamConfig(review_every_runs=1).review_every_runs == 1
        assert (
            DreamConfig.model_validate({"reviewEveryRuns": 1}).review_every_runs == 1
        )


class TestUnknownForms:
    @pytest.mark.asyncio
    async def test_unknown_file_name_lists_the_valid_ones(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        out = await router.dispatch(_ctx(loop, "/dream budget wiki 6000"))

        assert _config_path(workspace).stat().st_mtime_ns == before
        assert "Unknown budget `wiki`" in out.content
        for name in ("memory", "user", "soul", "review"):
            assert f"`{name}`" in out.content

    @pytest.mark.asyncio
    async def test_unknown_subcommand_lists_the_valid_forms(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream nuke"))

        assert "Unknown `/dream` argument `nuke`" in out.content
        assert "Valid forms:" in out.content
        assert "`/dream budget`" in out.content

    @pytest.mark.asyncio
    async def test_a_missing_value_says_so(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget memory"))

        assert "is missing a value" in out.content
        assert "Valid forms:" in out.content

    @pytest.mark.asyncio
    async def test_too_many_words_says_so(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000 please"))

        assert "at most a name and a value" in out.content

    @pytest.mark.asyncio
    async def test_the_subcommand_is_case_insensitive(self, router, loop, workspace):
        out = await router.dispatch(_ctx(loop, "/dream BUDGET Memory 6000"))

        assert _dream_config(workspace).memory_budget_chars == 6000
        assert "6,000 chars" in out.content
