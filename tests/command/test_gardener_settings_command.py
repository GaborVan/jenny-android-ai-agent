"""``/gardener settings|off|on|interval|idle|distance|compact``.

Prima di questo comando niente in ``jenny/webui/`` o ``jenny/command/`` leggeva o
scriveva ``agents.defaults.gardener``: ``enabled=False`` — la via d'uscita
documentata, e la ragione per cui esiste il cancello di dispatch in
``CronDispatcher._run_gardener`` — non era raggiungibile da nessuna superficie.
L'unico modo di spegnere il giardiniere era una shell di root sul telefono, ed è
così che ``compactProjectsWhenIdle`` è arrivato acceso: scritto a mano fuori da
``store.mutate()``, e spento con un ``sed -i`` che ha rotto l'etichetta SELinux
del file.

Quindi qui si verificano tre cose, e la terza è quella che il resto della suite
non può controllare da sola:

1. ogni sotto-comando scrive e **dice cosa è cambiato** (un comando che risponde
   "fatto" non si può distinguere da uno che non ha fatto niente);
2. un valore fuori range viene rifiutato **nominando il range**, e senza toccare
   il file;
3. la scrittura passa da ``store.mutate()``. Non è uno dettaglio di stile: v.
   ``test_an_unknown_config_key_survives_the_write``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jenny.bus.events import InboundMessage
from jenny.command.builtin import cmd_gardener
from jenny.command.router import CommandContext
from jenny.config.loader import _backup_path, save_config
from jenny.config.schema import (
    GARDENER_DISTANCE_HOURS_MAX,
    GARDENER_IDLE_MIN_MAX,
    GARDENER_INTERVAL_MIN_MAX,
    Config,
)

_PROVIDER = {
    "default": "ds",
    "providers": [{"name": "ds", "format": "openai_compat", "api_key": "sk-secret"}],
}


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un ``config.json`` vero sotto ``tmp_path``, con un provider dentro.

    Il provider non è decorazione: due test qui misurano che una scrittura del
    giardiniere non se lo porti via.
    """
    from jenny.config import paths
    from jenny.runtime.context import get_runtime_context

    path = tmp_path / "config.json"
    previous = paths.get_workspace_path()
    paths.set_workspace_dir(str(tmp_path))
    monkeypatch.setattr(get_runtime_context(), "config_path", path)
    config = Config.model_validate({"providers": _PROVIDER})
    save_config(config, path)
    yield path
    paths.set_workspace_dir(str(previous))


@pytest.fixture()
def cron(tmp_path: Path):
    from jenny.cron.service import CronService

    return CronService(store_path=tmp_path / "cron" / "jobs.json")


@pytest.fixture()
def run(cron):
    """Lancia ``/gardener <args>`` e restituisce il testo della risposta."""

    async def _run(args: str, *, key: str = "unified:default", loop=None) -> str:
        msg = InboundMessage(
            channel="websocket", sender_id="user", chat_id="c1",
            content=f"/gardener {args}".strip(),
        )
        ctx = CommandContext(
            msg=msg, session=None, key=key, raw=msg.content, args=args,
            loop=loop or SimpleNamespace(bus=None, cron_service=cron),
        )
        return (await cmd_gardener(ctx)).content

    return _run


def _gardener(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["agents"]["defaults"]["gardener"]


def _defaults(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["agents"]["defaults"]


# -- lettura -------------------------------------------------------------------


class TestReading:
    async def test_settings_shows_all_three_numbers_with_their_ranges(self, run, config_path):
        """La vista di lettura ha bisogno di una parola sua: ``/gardener`` senza
        argomento **lancia una passata**, quindi non c'è nessun modo di rileggere i
        numeri appena scritti se non nominandolo — che è il problema (cercare i
        valori in `config.json`) da cui nasce questo comando."""
        text = await run("settings")

        assert "**On**" in text
        for name in ("interval", "idle", "distance"):
            assert f"`{name}`" in text
        assert f"(1–{GARDENER_INTERVAL_MIN_MAX})" in text
        assert f"(0–{GARDENER_IDLE_MIN_MAX})" in text
        assert f"(0–{GARDENER_DISTANCE_HOURS_MAX})" in text

    async def test_settings_does_not_write(self, run, config_path):
        before = config_path.read_bytes()

        await run("settings")

        assert config_path.read_bytes() == before
        assert not _backup_path(config_path).exists()

    async def test_a_disabled_gardener_says_what_still_works_by_hand(self, run, config_path):
        """Spegnere non è disinstallare, e chi legge solo "off" non ha modo di
        saperlo: la strada a mano è quella che ha reso collaudabile la feature."""
        await run("off")

        text = await run("settings")

        assert "**Off**" in text
        assert "/gardener <project>` still work by hand" in text


# -- scrittura -----------------------------------------------------------------


class TestWriting:
    async def test_off_writes_the_switch_and_says_what_still_works(self, run, config_path):
        text = await run("off")

        assert _gardener(config_path)["enabled"] is False
        assert "The gardener is off" in text
        assert "still work by hand" in text

    async def test_on_writes_the_switch_and_repeats_the_schedule(self, run, config_path):
        await run("off")

        text = await run("on")

        assert _gardener(config_path)["enabled"] is True
        assert "The gardener is on" in text
        assert "every 30min" in text

    @pytest.mark.parametrize(
        ("args", "key", "value"),
        [
            ("interval 120", "intervalMin", 120),
            ("idle 45", "idleMin", 45),
            ("distance 24", "minHoursBetweenPasses", 24),
        ],
    )
    async def test_each_number_lands_in_the_file(self, run, config_path, args, key, value):
        await run(args)

        assert _gardener(config_path)[key] == value

    @pytest.mark.parametrize(
        ("args", "before", "after"),
        [("interval 120", "30", "120"), ("idle 45", "30", "45"), ("distance 24", "6", "24")],
    )
    async def test_each_number_reports_the_before_and_the_after(
        self, run, config_path, args, before, after
    ):
        """Un comando che risponde "fatto" non si distingue da uno che non ha fatto
        niente. I due numeri sono il modo più corto di dire che è cambiato."""
        text = await run(args)

        assert f"{before} → {after}" in text

    async def test_a_clock_set_to_zero_says_what_that_costs(self, run, config_path):
        """``0`` è legittimo su entrambi gli orologi, e su entrambi rimuove una
        difesa: ``idle 0`` fa entrare il giardiniere mentre stai parlando."""
        text = await run("idle 0")

        assert _gardener(config_path)["idleMin"] == 0
        assert "while you are talking" in text

    async def test_a_number_written_while_it_is_off_says_nothing_is_looking(
        self, run, config_path
    ):
        """Tarare una cosa spenta e non vedere effetti è indistinguibile da una
        manopola rotta. Il valore si scrive comunque — è un numero della passata
        periodica anche a passata ferma — ma il motivo va detto da qui."""
        await run("off")

        text = await run("interval 120")

        assert _gardener(config_path)["intervalMin"] == 120
        assert "the gardener is off" in text
        assert "`/gardener on`" in text

    async def test_writing_the_same_value_again_does_not_rewrite_the_file(
        self, run, config_path
    ):
        """Il contratto di ``mutate``: la callback che ritorna ``False`` lascia il
        file intatto, così un comando ribattuto non riscrive `config.json` né ruota
        il `.bak` per nulla — su Android ogni riscrittura è un `.bak` in più e una
        `chmod` da rifare."""
        await run("interval 120")
        snapshot = config_path.read_bytes()
        _backup_path(config_path).unlink(missing_ok=True)

        text = await run("interval 120")

        assert "already 120 min" in text
        assert "was not rewritten" in text
        assert config_path.read_bytes() == snapshot
        assert not _backup_path(config_path).exists()


# -- il flag piu' pesante ------------------------------------------------------


class TestProjectHistoryCompaction:
    """``compactProjectsWhenIdle`` è **il valore che è arrivato acceso sul device
    passando fuori da** ``store.mutate()``: scritto a mano, e spento con un
    ``sed -i`` che ha rotto l'etichetta SELinux del file. Averlo qui è il rimedio a
    quella classe di incidente, non una comodità."""

    async def test_compact_on_writes_the_flag_and_names_what_is_lost(self, run, config_path):
        text = await run("compact on")

        assert _defaults(config_path)["compactProjectsWhenIdle"] is True
        assert "**on**" in text
        # Cosa si perde e cosa no: l'amnesia è dell'agente, non del registro.
        assert "not what was said" in text
        assert "transcript is untouched" in text

    async def test_compact_off_writes_the_flag(self, run, config_path):
        await run("compact on")

        text = await run("compact off")

        assert _defaults(config_path)["compactProjectsWhenIdle"] is False
        assert "**off**" in text

    async def test_it_says_when_it_takes_effect(self, run, config_path):
        """Il flag lo legge ``AgentLoop`` quando costruisce ``AutoCompact``, quindi
        scriverlo non lo applica al processo in corso. Una manopola che sembra fatta
        e non è fatta è peggio di una che dichiara quando ha effetto."""
        text = await run("compact on")

        assert "next gateway start" in text

    async def test_compact_without_a_value_is_refused(self, run, config_path):
        text = await run("compact")

        assert "needs `on` or `off`" in text
        assert _defaults(config_path)["compactProjectsWhenIdle"] is False


# -- rifiuti -------------------------------------------------------------------


class TestRefusals:
    @pytest.mark.parametrize(
        ("args", "low", "high"),
        [
            ("interval 0", 1, GARDENER_INTERVAL_MIN_MAX),
            (f"interval {GARDENER_INTERVAL_MIN_MAX + 1}", 1, GARDENER_INTERVAL_MIN_MAX),
            ("idle -1", 0, GARDENER_IDLE_MIN_MAX),
            (f"idle {GARDENER_IDLE_MIN_MAX + 1}", 0, GARDENER_IDLE_MIN_MAX),
            ("distance -1", 0, GARDENER_DISTANCE_HOURS_MAX),
            (
                f"distance {GARDENER_DISTANCE_HOURS_MAX + 1}",
                0,
                GARDENER_DISTANCE_HOURS_MAX,
            ),
        ],
    )
    async def test_a_value_outside_the_range_is_refused_naming_the_range(
        self, run, config_path, args, low, high
    ):
        """Il rifiuto dice i due numeri. Senza di essi l'utente ritenta a caso, e il
        range è precisamente l'informazione che non ha nessun altro modo di
        leggere."""
        before = config_path.read_bytes()

        text = await run(args)

        assert f"between {low} and {high}" in text
        # Un input sbagliato non entra in ``mutate``: niente lock, niente `.bak`.
        assert config_path.read_bytes() == before
        assert not _backup_path(config_path).exists()

    async def test_a_refusal_points_at_the_reversible_way_to_say_never(
        self, run, config_path
    ):
        """Un tetto che dice solo "no" manda l'utente a cercare un numero più
        grande. Quel che voleva — "non farlo più" — ha già un interruttore, e
        reversibile."""
        text = await run(f"interval {GARDENER_INTERVAL_MIN_MAX + 1}")

        assert "`/gardener off`" in text

    async def test_a_value_that_is_not_a_number_says_the_usage(self, run, config_path):
        text = await run("interval presto")

        assert "`presto` is not a whole number" in text
        assert "Usage: `/gardener interval <min>`" in text

    async def test_a_missing_value_is_not_read_as_zero(self, run, config_path):
        text = await run("interval")

        assert "is missing a value" in text
        assert _gardener(config_path)["intervalMin"] == 30

    async def test_two_values_are_refused_rather_than_the_first_one_taken(
        self, run, config_path
    ):
        text = await run("interval 120 45")

        assert "takes one value" in text
        assert _gardener(config_path)["intervalMin"] == 30

    async def test_a_switch_with_an_argument_is_refused(self, run, config_path):
        """``/gardener off adesso`` non deve diventare né uno spegnimento né una
        passata sul progetto "adesso"."""
        text = await run("off adesso")

        assert "takes no value" in text
        assert _gardener(config_path)["enabled"] is True

    async def test_every_refusal_lists_the_valid_forms(self, run, config_path):
        """La lista è l'unico posto in cui si scopre che ``distance`` e ``compact``
        esistono, e chi ha appena sbagliato la sintassi ne ha bisogno."""
        for args in ("interval 0", "interval presto", "compact", "off adesso"):
            text = await run(args)
            assert "Valid forms:" in text, args


# -- la via d'uscita da una config che lo schema di oggi boccerebbe ------------


async def test_off_works_from_a_config_the_schema_would_now_reject(run, config_path):
    """La domanda che decide dove va il tetto.

    Un ``intervalMin`` fuori range scritto da una versione senza tetti farebbe
    fallire il parse; ``loader._load_with_recovery`` proverebbe il ``.bak`` (stesso
    numero), poi metterebbe il file in quarantena e ripartirebbe dai **default**.
    E ``store.mutate`` rilegge il file dentro il proprio lock: ``/gardener off``
    scriverebbe quindi *sopra* una config azzerata, cioè lo spegnimento
    cancellerebbe il provider e la sua chiave.

    ``GardenerConfig.clamp_raw`` è ciò che tiene aperta questa strada.
    """
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["agents"]["defaults"]["gardener"]["intervalMin"] = 10 ** 9
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    text = await run("off")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["agents"]["defaults"]["gardener"]["enabled"] is False
    assert "The gardener is off" in text
    # La cosa che la quarantena avrebbe portato via.
    assert saved["providers"]["providers"][0]["apiKey"] == "sk-secret"
    # E il numero impossibile è stato riportato dentro il tetto dalla scrittura,
    # invece di restare a fare da mina per il prossimo che aggiunge un vincolo.
    assert (
        saved["agents"]["defaults"]["gardener"]["intervalMin"]
        == GARDENER_INTERVAL_MIN_MAX
    )


# -- la scrittura passa dal funnel --------------------------------------------


async def test_an_unknown_config_key_survives_the_write(run, config_path):
    """Il test che muore se qualcuno rimpiazza ``mutate()`` con ``save_config()``.

    ``save_config`` riscrive il file intero dal dump dello schema: senza
    ``preserve_unknown_from`` — che passa solo ``store.mutate`` — ogni chiave che
    questa versione non conosce viene cancellata. Sono le impostazioni di una
    versione più nuova, e su un telefono che ha aggiornato e poi è tornato indietro
    sparirebbero al primo ``/gardener interval``.

    È anche il test che copre l'altra metà della regola, quella che nessun test
    può cogliere per te: ``mutate`` rilegge il file *dentro* il lock che usa per
    scrivere, quindi nessun chiamante può tenere in mano una copia vecchia.
    """
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["somethingFromANewerVersion"] = {"keep": "me"}
    config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    await run("interval 120")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["somethingFromANewerVersion"] == {"keep": "me"}
    assert saved["agents"]["defaults"]["gardener"]["intervalMin"] == 120


async def test_the_write_keeps_the_file_private(run, config_path):
    """Dopo la scrittura atomica il ``chmod 600`` viene ripristinato: nel file c'è
    la chiave del provider, e un `config.json` leggibile a tutti è una regressione
    di sicurezza silenziosa. Il ripristino lo fa ``save_config`` in fondo al
    funnel, quindi questo test non distingue ``mutate`` da una chiamata diretta —
    quello lo fa ``test_an_unknown_config_key_survives_the_write`` — ma copre la
    strada che un `chmod` fatto a mano fuori dal funnel romperebbe (è già
    successo: un ``sed -i`` sul telefono)."""
    config_path.chmod(0o600)

    await run("interval 120")

    assert config_path.stat().st_mode & 0o777 == 0o600


# -- il cron vede la scrittura senza riavvio ----------------------------------


class TestTheCronSeesIt:
    """La conferma in chat non basta: ``interval_min`` non vive nel ``Config`` al
    momento del tick — è lo ``schedule`` del ``CronJob`` nello store del cron — e su
    un gateway partito col giardiniere spento il job non è nemmeno registrato. Il
    dettaglio dei due casi sta in
    ``tests/cron/test_gardener_settings_reach_the_dispatcher.py``; qui si verifica
    che sia il **comando** a chiudere il cerchio, e che lo dica."""

    def _job(self, cron):
        from jenny.runtime.cron_dispatch import GARDENER_JOB_ID

        return next((j for j in cron._load_store().jobs if j.id == GARDENER_JOB_ID), None)

    async def test_a_new_interval_re_arms_the_job_and_says_no_restart_is_needed(
        self, run, config_path, cron
    ):
        text = await run("interval 120")

        assert self._job(cron).schedule.every_ms == 120 * 60_000
        assert "no restart needed" in text

    async def test_on_registers_the_job_that_a_disabled_startup_never_created(
        self, run, config_path, cron
    ):
        await run("off")
        assert self._job(cron) is None

        text = await run("on")

        assert self._job(cron) is not None
        assert "no restart needed" in text

    async def test_without_a_cron_service_the_value_is_still_written(
        self, run, config_path
    ):
        """Un loop costruito senza servizio cron — un test, un percorso che non è il
        gateway — non è un errore: il numero si scrive, e la riga sul riarmo
        semplicemente non c'è."""
        loop = SimpleNamespace(bus=None)

        text = await run("interval 120", loop=loop)

        assert _gardener(config_path)["intervalMin"] == 120
        assert "no restart needed" not in text


# -- il ramo che non deve essere mangiato --------------------------------------


class TestTheProjectBranchIsIntact:
    """Le parole riservate stanno dove prima c'era solo il nome di un progetto: se
    il nuovo ramo prendesse un argomento in più del suo, ``/gardener <project>``
    smetterebbe di lanciare passate — cioè la feature originale."""

    @pytest.fixture()
    def no_background(self, monkeypatch):
        started: list[str] = []

        def _swallow(coro):
            started.append(getattr(coro, "__name__", "coro"))
            coro.close()
            return None

        monkeypatch.setattr(asyncio, "create_task", _swallow)
        return started

    async def test_a_named_project_still_starts_a_pass(self, run, config_path, no_background):
        text = await run("viaggio")

        assert "Gardening viaggio" in text
        assert no_background

    async def test_inside_a_project_no_argument_still_starts_a_pass(
        self, run, config_path, no_background
    ):
        text = await run("", key="project:viaggio")

        assert "Gardening viaggio" in text
        assert no_background

    async def test_a_project_named_like_a_subcommand_is_shadowed_and_the_usage_says_so(
        self, run, config_path
    ):
        """Comportamento dichiarato, non incidente: un progetto chiamato ``off``
        viene oscurato, e il rimedio (``/gardener`` da dentro il progetto, che non
        passa dal nome) sta scritto nella lista delle forme valide."""
        text = await run("off")

        assert "The gardener is off" in text
        assert "shadowed by these forms" in await run("settings")
