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

from jenny.agent.memory import MemoryStore
from jenny.bus.events import InboundMessage
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
    async def test_reading_does_not_rewrite_config(self, router, loop, workspace):
        before = _config_path(workspace).stat().st_mtime_ns

        await router.dispatch(_ctx(loop, "/dream budget"))

        assert _config_path(workspace).stat().st_mtime_ns == before


class TestWrite:
    @pytest.mark.asyncio
    async def test_a_budget_reaches_the_file_on_disk(self, router, loop, workspace):
        out = await router.dispatch(_ctx(loop, "/dream budget memory 6000"))

        assert _dream_config(workspace).memory_budget_chars == 6000
        # Camel case: è la forma con cui il device legge la config.
        raw = json.loads(_config_path(workspace).read_text(encoding="utf-8"))
        assert raw["agents"]["defaults"]["dream"]["memoryBudgetChars"] == 6000
        assert "0 → 6,000 chars" in out.content

    @pytest.mark.asyncio
    async def test_each_name_writes_its_own_field(self, router, loop, workspace):
        await router.dispatch(_ctx(loop, "/dream budget user 1500"))
        await router.dispatch(_ctx(loop, "/dream budget soul 900"))
        await router.dispatch(_ctx(loop, "/dream budget review 4"))

        dream = _dream_config(workspace)
        assert (dream.user_budget_chars, dream.soul_budget_chars) == (1500, 900)
        assert dream.review_every_runs == 4
        assert dream.memory_budget_chars == 0

    @pytest.mark.asyncio
    async def test_review_confirmation_speaks_in_runs(self, router, loop):
        out = await router.dispatch(_ctx(loop, "/dream budget review 4"))

        assert "every 12 → every 4 runs" in out.content

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
        assert _dream_config(workspace).memory_budget_chars == 0
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
