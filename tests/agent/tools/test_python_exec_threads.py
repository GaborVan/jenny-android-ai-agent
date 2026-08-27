"""R8 + R9 — ciò che succede quando ``python_exec`` cambia thread.

Due difetti diversi con la stessa radice: il guard di ``python_exec`` vive in
un ``threading.local()`` e l'esecuzione vera avviene su thread che non sono
quello dell'event loop.

**R9 — un lock globale su un pool condiviso, non interrompibile.**
``execute()`` teneva un lock di processo per TUTTA la finestra guardata (fino a
600 s per un one-shot, illimitato per una exec session) e il secondo
``python_exec`` si parcheggiava su ``lock.acquire()`` DENTRO un worker del
default executor. ``asyncio.wait_for`` scadeva e chiamava
``PyThreadState_SetAsyncExc``, che non raggiunge un thread fermo in un lock a
livello C: la coroutine tornava un ordinato "timed out" al modello e il thread
restava bloccato per sempre. Il default executor ha ~12 worker sul device ed è
condiviso con le ~50 ``asyncio.to_thread`` di ``jenny/`` — snapshot, backup,
notifier, cron. Riprodotto con un pool piccolo, che è la forma giusta::

    [1.3s] python_exec results: ['Error: Python execution timed out after 1 seconds', ...x2]
    [3.3s] unrelated asyncio.to_thread: *** STARVED (executor exhausted) ***

Due misure, e i test qui sotto le tengono ferme entrambe: il pool DEDICATO
(nessun exec può più togliere un worker al notifier) e la cattura di stdout
PER THREAD (nessun lock da tenere, quindi nessun parcheggio da cui non si
torna).

**R8 — il confine si ferma al salto di thread.** ``asyncio`` è nell'allowlist
di default e ``asyncio.to_thread`` esegue su un altro thread, dove
``_active_path_boundary()`` è ``None`` e ogni wrapper passa dritto. Attenzione
alla forma: ``to_thread(open(p).read)`` NON evade (``open(p)`` è valutata sul
thread guardato), serve che l'apertura avvenga dopo il salto. Entrambe le
forme sono qui.
"""

from __future__ import annotations

import asyncio
import io
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from jenny.agent.tools import python_exec
from jenny.agent.tools.python_exec import PythonNamespace, run_python_async
from jenny.config.tool_schemas import PythonExecConfig

_REFUSED = "outside allowed directory"


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def outside_secret(tmp_path):
    """Un file FUORI dal workspace, con dentro qualcosa di riconoscibile."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("TOP-SECRET")
    return secret


def _namespace(workspace_dir, *, restrict: bool = True) -> PythonNamespace:
    cfg = PythonExecConfig()
    return PythonNamespace(
        working_dir=str(workspace_dir),
        allowed_modules=cfg.allowed_modules,
        blocked_modules=cfg.blocked_modules,
        restrict_to_workspace=restrict,
        workspace=str(workspace_dir),
    )


@pytest.fixture
def small_exec_pool(monkeypatch):
    """Sostituisce il pool di python_exec con uno da 2 worker.

    Stesso trucco della riproduzione del reviewer: con un pool piccolo
    l'esaurimento si vede in un secondo invece che dopo dodici exec incastrati.
    Il pool vero è un singleton di modulo e non va sporcato.
    """
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test_python_exec")
    monkeypatch.setattr(python_exec, "_exec_pool", pool)
    yield pool
    pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------------------
# R9 — l'esaurimento resta dentro il tool
# ---------------------------------------------------------------------------


class TestExecutorIsolation:
    async def test_wedged_execs_cannot_starve_the_default_executor(
        self, workspace, small_exec_pool
    ):
        """Il test che avrebbe colto R9.

        Tre esecuzioni che si incastrano in una attesa a livello C (cioè non
        interrompibili da ``SetAsyncExc``) su un pool da 2: prima del fix
        andavano nel default executor e portavano con sé notifier, snapshot,
        backup e cron. Ora il default executor deve restare libero.
        """
        loop = asyncio.get_running_loop()
        default_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test_default")
        loop.set_default_executor(default_pool)
        release = threading.Event()

        def _block() -> str:
            # Attesa in C: `PyThreadState_SetAsyncExc` non la raggiunge, che è
            # esattamente il caso che rendeva il thread uno zombie.
            release.wait(30)
            return "released"

        namespaces = []
        for _ in range(3):
            ns = _namespace(workspace)
            ns.register_function("block", _block)
            namespaces.append(ns)

        try:
            results = await asyncio.gather(*[
                run_python_async(None, "block", None, None, ns, 1, 10_000)
                for ns in namespaces
            ])
            assert all("timed out" in r for r in results), results

            # Il default executor non è stato sfiorato: una to_thread
            # qualunque (il notifier, lo snapshot, il cron) deve rispondere.
            host = await asyncio.wait_for(asyncio.to_thread(lambda: "host ran"), timeout=5)
            assert host == "host ran"
        finally:
            release.set()
            default_pool.shutdown(wait=False)

    async def test_one_shot_execution_runs_on_the_dedicated_pool(self, workspace):
        """Diretta e a buon mercato: il nome del thread dice quale pool è."""
        ns = _namespace(workspace, restrict=False)
        out = await run_python_async(
            "import sys\nsys.modules['threading'].current_thread().name",
            None, None, None, ns, 10, 10_000,
        )
        assert "python_exec" in out


# ---------------------------------------------------------------------------
# R9 — niente più lock di processo sulla finestra guardata
# ---------------------------------------------------------------------------


class TestNoGlobalSerialisation:
    async def test_a_slow_execution_does_not_block_a_second_one(self, workspace):
        """Prima del fix la seconda chiamata aspettava la prima, sempre."""
        release = threading.Event()
        slow = _namespace(workspace, restrict=False)
        slow.register_function("hold", lambda: release.wait(10))
        fast = _namespace(workspace, restrict=False)

        slow_task = asyncio.create_task(
            run_python_async(None, "hold", None, None, slow, 10, 10_000)
        )
        await asyncio.sleep(0.1)
        started = time.monotonic()
        out = await asyncio.wait_for(
            run_python_async("2 + 2", None, None, None, fast, 5, 10_000), timeout=3
        )
        elapsed = time.monotonic() - started
        release.set()
        await slow_task

        assert "4" in out
        assert elapsed < 1.0, f"the second execution waited {elapsed:.2f}s for the first"

    def test_concurrent_executions_keep_their_own_output(self, workspace):
        """La cattura per-thread sostituisce il lock: stessa garanzia, senza coda."""
        results: dict[str, tuple[str, str]] = {}
        barrier = threading.Barrier(2)

        def _run(tag: str) -> None:
            ns = _namespace(workspace, restrict=False)
            barrier.wait(5)
            code = (
                "import time\n"
                f"for i in range(5):\n    print('{tag}-' + str(i))\n    time.sleep(0.01)\n"
            )
            stdout, stderr, _ = ns.execute(code)
            results[tag] = (stdout, stderr)

        threads = [threading.Thread(target=_run, args=(tag,)) for tag in ("A", "B")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        assert "A-0" in results["A"][0] and "B" not in results["A"][0]
        assert "B-0" in results["B"][0] and "A" not in results["B"][0]

    def test_host_threads_keep_writing_to_the_real_stream(self, workspace, monkeypatch):
        """Il proxy è trasparente: senza buffer sul thread, scrive dove sempre.

        È la proprietà che il vecchio ``redirect_stdout`` non poteva avere:
        mutando ``sys.stdout`` a livello di processo, per tutta la finestra
        guardata QUALUNQUE thread che stampasse finiva nel buffer dell'exec.
        """
        host_stream = io.StringIO()
        monkeypatch.setattr(sys, "stdout", host_stream)
        release = threading.Event()
        started = threading.Event()

        ns = _namespace(workspace, restrict=False)

        def _hold() -> None:
            print("EXEC-SIDE")
            started.set()
            release.wait(10)

        ns.register_function("hold", _hold)
        captured: list[str] = []
        worker = threading.Thread(target=lambda: captured.append(ns.call_function("hold")[0]))
        worker.start()
        try:
            assert started.wait(5)
            print("HOST-SIDE")
        finally:
            release.set()
            worker.join(10)

        assert host_stream.getvalue().strip() == "HOST-SIDE"
        assert captured == ["EXEC-SIDE\n"]


# ---------------------------------------------------------------------------
# R8 — il confine attraversa il salto di thread
# ---------------------------------------------------------------------------


class TestBoundaryCrossesThreadHops:
    def test_to_thread_with_a_deferred_open_is_refused(self, workspace, outside_secret):
        """La forma che evade davvero: l'apertura avviene DOPO il salto."""
        code = (
            "import asyncio, pathlib\n"
            "async def main():\n"
            f"    return await asyncio.to_thread(pathlib.Path({str(outside_secret)!r}).read_text)\n"
            "print('LEAKED:', asyncio.run(main()))\n"
        )
        stdout, stderr, _ = _namespace(workspace).execute(code)
        assert "TOP-SECRET" not in stdout
        assert _REFUSED in stderr

    def test_to_thread_with_a_lambda_is_refused(self, workspace, outside_secret):
        code = (
            "import asyncio\n"
            "async def main():\n"
            f"    return await asyncio.to_thread(lambda: open({str(outside_secret)!r}).read())\n"
            "print('LEAKED:', asyncio.run(main()))\n"
        )
        stdout, stderr, _ = _namespace(workspace).execute(code)
        assert "TOP-SECRET" not in stdout
        assert _REFUSED in stderr

    def test_to_thread_with_an_eager_open_was_already_refused(self, workspace, outside_secret):
        """Correzione al write-up del reviewer, tenuta ferma da un test.

        ``to_thread(open(p).read)`` valuta ``open(p)`` sul thread GUARDATO: era
        già rifiutato prima di questo fix. Il test resta perché è l'esempio che
        fa sbagliare diagnosi.
        """
        code = (
            "import asyncio\n"
            "async def main():\n"
            f"    return await asyncio.to_thread(open({str(outside_secret)!r}).read)\n"
            "print('LEAKED:', asyncio.run(main()))\n"
        )
        stdout, stderr, _ = _namespace(workspace).execute(code)
        assert "TOP-SECRET" not in stdout
        assert _REFUSED in stderr

    def test_run_in_executor_is_refused(self, workspace, outside_secret):
        code = (
            "import asyncio, pathlib\n"
            "async def main():\n"
            "    loop = asyncio.get_running_loop()\n"
            "    return await loop.run_in_executor("
            f"None, pathlib.Path({str(outside_secret)!r}).read_text)\n"
            "print('LEAKED:', asyncio.run(main()))\n"
        )
        stdout, stderr, _ = _namespace(workspace).execute(code)
        assert "TOP-SECRET" not in stdout
        assert _REFUSED in stderr

    def test_a_write_inside_the_workspace_still_works_after_the_hop(self, workspace):
        """Il confine viaggia, la capability no: dentro il workspace si lavora."""
        code = (
            "import asyncio, pathlib\n"
            "async def main():\n"
            "    return await asyncio.to_thread(pathlib.Path('hop.txt').write_text, 'ok')\n"
            "asyncio.run(main())\n"
        )
        _, stderr, _ = _namespace(workspace).execute(code)
        assert stderr == ""
        assert (workspace / "hop.txt").read_text() == "ok"

    def test_output_printed_after_the_hop_still_reaches_the_model(self, workspace):
        """Col redirect globale l'output del worker arrivava; deve continuare.

        La cattura per-thread lo perderebbe: un
        ``await asyncio.to_thread(qualcosa_che_stampa)`` diventerebbe muto.
        """
        code = (
            "import asyncio\n"
            "def work():\n    print('FROM-WORKER')\n    return 1\n"
            "async def main():\n    return await asyncio.to_thread(work)\n"
            "asyncio.run(main())\n"
        )
        stdout, stderr, _ = _namespace(workspace).execute(code)
        assert stderr == ""
        assert "FROM-WORKER" in stdout

    def test_module_rules_travel_too(self, workspace):
        """Non solo il path: gli stub di evasione di ``os`` valgono anche lì."""
        code = (
            "import asyncio, os\n"
            "async def main():\n"
            "    return await asyncio.to_thread(os.system, 'echo nope')\n"
            "asyncio.run(main())\n"
        )
        _, stderr, _ = _namespace(workspace, restrict=False).execute(code)
        assert "not available on this platform" in stderr

    def test_the_worker_is_left_clean_even_when_the_call_raises(
        self, workspace, outside_secret
    ):
        """Lo stato installato sul worker va smontato anche sull'eccezione.

        È la perdita che ``_enter_guard`` teme al contrario: un worker riciclato
        che torna nel pool con il confine ancora acceso farebbe fallire ogni
        ``asyncio.to_thread`` dell'host che ci finisce sopra.
        """
        code = (
            "import asyncio, pathlib\n"
            "async def main():\n"
            "    try:\n"
            f"        await asyncio.to_thread(pathlib.Path({str(outside_secret)!r}).read_text)\n"
            "    except OSError:\n"
            "        pass\n"
            "asyncio.run(main())\n"
        )
        _namespace(workspace).execute(code)

        # Sullo stesso pool (il default executor), l'host deve poter leggere di
        # nuovo qualunque cosa.
        async def _host_read() -> str:
            return await asyncio.to_thread(outside_secret.read_text)

        assert asyncio.run(_host_read()) == "TOP-SECRET"

    def test_the_patch_is_inert_for_host_code(self, outside_secret):
        """Nessun guard sul thread chiamante ⇒ nulla cambia, in nessun verso."""

        async def _host() -> tuple[str, str]:
            loop = asyncio.get_running_loop()
            return (
                await asyncio.to_thread(outside_secret.read_text),
                await loop.run_in_executor(None, outside_secret.read_text),
            )

        assert asyncio.run(_host()) == ("TOP-SECRET", "TOP-SECRET")


# ---------------------------------------------------------------------------
# R8 — quel che resta aperto, scritto a chiare lettere
# ---------------------------------------------------------------------------


class TestKnownRemainingDoors:
    """``asyncio`` non è l'unica porta, e il file lo dice: qui è misurato.

    ``threading`` e ``concurrent.futures`` NON sono nell'allowlist e
    ``import threading`` viene rifiutato — ma un modulo consentito che li
    importa al proprio interno li espone come attributi. Coprirli vorrebbe dire
    patchare ``threading.Thread`` a livello di processo, cioè mettersi in mezzo
    a ogni thread del gateway per contenere codice che nessun modello scrive
    per sbaglio; vedi il commento TRUST BOUNDARY in testa a ``python_exec.py``.

    Se un giorno queste asserzioni falliranno vuol dire che qualcuno ha chiuso
    anche queste porte: bene — ma allora vanno aggiornati sia questo test sia
    il commento su ``_patch_asyncio_thread_hops``, che oggi le dichiara aperte.
    """

    def test_import_threading_is_still_refused(self, workspace):
        _, stderr, _ = _namespace(workspace).execute("import threading")
        assert "not in the allowed modules list" in stderr

    def test_import_concurrent_futures_is_still_refused(self, workspace):
        _, stderr, _ = _namespace(workspace).execute("import concurrent.futures")
        assert "not in the allowed modules list" in stderr

    def test_a_raw_thread_reached_through_asyncio_still_escapes(
        self, workspace, outside_secret
    ):
        code = (
            "import asyncio, pathlib\n"
            "box = []\n"
            "T = asyncio.base_events.threading\n"
            f"t = T.Thread(target=lambda: box.append(pathlib.Path({str(outside_secret)!r})"
            ".read_text()))\n"
            "t.start(); t.join()\n"
            "print('ESCAPED:', box[0])\n"
        )
        stdout, _, _ = _namespace(workspace).execute(code)
        assert "ESCAPED: TOP-SECRET" in stdout


# ---------------------------------------------------------------------------
# T4.13 — un salto porta DUE metà, e portarne una era ancora un buco
# ---------------------------------------------------------------------------
#
# Le classi qui sopra provano la metà THREAD-LOCAL del salto (il confine di
# percorso), e la provano in modo **sincrono**: chiamano
# ``PythonNamespace.execute()`` dal thread del test. Per quella metà va bene —
# `threading.local` ce l'ha anche il thread del test.
#
# L'altra metà è un ContextVar, e per quella una prova sincrona non dice niente:
# in produzione il codice del modello gira su un worker raggiunto con
# ``loop.run_in_executor``, che NON copia il contesto. Le due politiche del turno
# — la sola lettura e il confine di scrittura dello scope — vivono là.
#
# MISURATO il 23/08, prima di T4.13, attraverso ``await PythonExecTool.execute()``:
#
#     await loop.run_in_executor(None, lambda: open(p, 'w').write('...'))
#
# scritto dal modello portava il thread-local e perdeva il ContextVar, quindi
# scriveva durante un turno in **sola lettura** e scriveva su ``SOUL.md`` da
# dentro una sessione-progetto. Cioè i difetti di T4.2 e T4.3 di nuovo, un salto
# più in fuori. ``asyncio.to_thread`` non era bucata, ma solo perché la
# ``to_thread`` della stdlib si copia il contesto da sé — copertura per caso.
#
# Quindi ogni test qui sotto passa dal **tool vero** e da ``await``, e ognuno
# verifica che il codice sia davvero atterrato su un ALTRO thread: un test che
# non salta non prova niente su un salto.


class TestBothHalvesCrossTheHop:
    @staticmethod
    def _tool(ws, *, restrict: bool = True):
        from jenny.agent.tools.python_exec import PythonExecTool
        from jenny.agent.tools.python_exec_builtins import _register_builtin_functions

        cfg = PythonExecConfig()
        tool = PythonExecTool(
            working_dir=str(ws),
            timeout=30,
            allowed_modules=cfg.allowed_modules,
            blocked_modules=cfg.blocked_modules,
            restrict_to_workspace=restrict,
            workspace=str(ws),
        )
        _register_builtin_functions(
            tool.namespace, workspace=str(ws), restrict_to_workspace=restrict
        )
        return tool

    @staticmethod
    def _hop_code(hop: str, target) -> str:
        """Codice del modello che scrive DOPO il salto, e dice se il salto c'è stato.

        ``T.get_ident`` invece di ``import threading``, che è rifiutato: è la
        stessa strada del test in ``TestKnownRemainingDoors``. Senza il confronto
        dei due ident un test che (per una qualunque ragione) esegue sul thread
        di partenza passerebbe raccontando di aver provato un salto.
        """
        launch = {
            "run_in_executor": (
                "    loop = asyncio.get_running_loop()\n"
                "    return await loop.run_in_executor(None, probe)\n"
            ),
            "to_thread": "    return await asyncio.to_thread(probe)\n",
        }[hop]
        return (
            "import asyncio\n"
            "T = asyncio.base_events.threading\n"
            "outer = T.get_ident()\n"
            "def probe():\n"
            "    try:\n"
            f"        open({str(target)!r}, 'w').write('BUCATO')\n"
            "        return (T.get_ident(), 'WROTE')\n"
            "    except BaseException as exc:\n"
            "        return (T.get_ident(), type(exc).__name__)\n"
            "async def main():\n"
            + launch
            + "inner, verdict = asyncio.run(main())\n"
            "print('SAME-THREAD' if inner == outer else 'HOPPED', verdict)\n"
        )

    @pytest.mark.parametrize("hop", ["run_in_executor", "to_thread"])
    @pytest.mark.parametrize("restrict", [True, False], ids=["restricted", "unrestricted"])
    async def test_a_read_only_turn_cannot_write_after_the_hop(
        self, tmp_path, hop: str, restrict: bool
    ):
        """La sola lettura è del TURNO, quindi vale anche oltre il salto.

        Parametrizzata su ``restrict_to_workspace`` per la ragione di T4.3: senza
        restrizione il confine di percorso non c'è, e la sola lettura non deve
        dipendere da lui.
        """
        import dataclasses

        from jenny.security.workspace_access import (
            build_workspace_scope,
            enter_workspace_scope,
        )

        target = tmp_path / "m.txt"
        target.write_text("prima\n", encoding="utf-8")
        scope = build_workspace_scope(tmp_path, "restricted").without_write_access()
        scope = dataclasses.replace(scope, restrict_to_workspace=restrict)

        with enter_workspace_scope(scope):
            out = await self._tool(tmp_path, restrict=restrict).execute(
                code=self._hop_code(hop, target)
            )

        assert "HOPPED" in out, f"il test non ha saltato niente: {out!r}"
        assert "ReadOnlyTurnError" in out, f"scrittura passata oltre il salto: {out!r}"
        assert target.read_text(encoding="utf-8") == "prima\n"

    @pytest.mark.parametrize("hop", ["run_in_executor", "to_thread"])
    async def test_a_project_scope_still_binds_writes_after_the_hop(self, tmp_path, hop: str):
        """Il confine di scrittura dello scope vale oltre il salto.

        Il tool è costruito sulla radice (la forma di ``AgentLoop``) e lo scope è
        su ``wikis/p``: oltre il salto lo scope tornava assente, quindi il
        confine tornava alla radice e da dentro un progetto si scriveva su
        ``SOUL.md``.
        """
        import dataclasses

        from jenny.security.workspace_access import (
            build_workspace_scope,
            enter_workspace_scope,
        )

        (tmp_path / "wikis" / "p" / "wiki").mkdir(parents=True)
        soul = tmp_path / "SOUL.md"
        soul.write_text("io\n", encoding="utf-8")
        scope = dataclasses.replace(
            build_workspace_scope(tmp_path, "restricted"),
            project_path=tmp_path / "wikis" / "p",
        )

        with enter_workspace_scope(scope):
            out = await self._tool(tmp_path).execute(code=self._hop_code(hop, soul))

        assert "HOPPED" in out, f"il test non ha saltato niente: {out!r}"
        assert soul.read_text(encoding="utf-8") == "io\n", (
            f"scritto fuori dal progetto oltre il salto: {out!r}"
        )

    @pytest.mark.parametrize("hop", ["run_in_executor", "to_thread"])
    async def test_a_writable_turn_still_writes_after_the_hop(self, tmp_path, hop: str):
        """La metà che conta: il ponte non deve chiudere il lavoro legittimo.

        Un ponte scritto troppo largo — «oltre il salto niente scritture» —
        passerebbe i due test qui sopra e renderebbe inutilizzabile
        ``asyncio.to_thread``, che è nell'allowlist perché il modello la usi.
        """
        from jenny.security.workspace_access import (
            build_workspace_scope,
            enter_workspace_scope,
        )

        target = tmp_path / "dentro.txt"
        target.write_text("prima\n", encoding="utf-8")

        with enter_workspace_scope(build_workspace_scope(tmp_path, "restricted")):
            out = await self._tool(tmp_path).execute(code=self._hop_code(hop, target))

        assert "HOPPED WROTE" in out, out
        assert target.read_text(encoding="utf-8") == "BUCATO"

    async def test_the_session_thread_carries_the_project_boundary(self, tmp_path):
        """Il ramo ``yield_time_ms``: un ``threading.Thread`` grezzo, non patchato.

        Non porta niente da sé — né ContextVar né thread-local — e da dentro
        ``exec_session`` non c'è modo di rimediare. Il ponte lo costruisce
        ``_ContextBoundNamespace`` sul thread dell'event loop.
        """
        import dataclasses

        from jenny.security.workspace_access import (
            build_workspace_scope,
            enter_workspace_scope,
        )

        (tmp_path / "wikis" / "p" / "wiki").mkdir(parents=True)
        soul = tmp_path / "SOUL.md"
        soul.write_text("io\n", encoding="utf-8")
        scope = dataclasses.replace(
            build_workspace_scope(tmp_path, "restricted"),
            project_path=tmp_path / "wikis" / "p",
        )

        with enter_workspace_scope(scope):
            out = await self._tool(tmp_path).execute(
                code=f"open({str(soul)!r}, 'w').write('BUCATO')",
                yield_time_ms=400,
            )

        assert soul.read_text(encoding="utf-8") == "io\n", (
            f"la sessione ha scritto fuori dal progetto: {out!r}"
        )


class TestTheGateKeepsTheHostOut:
    """``asyncio.to_thread`` e ``run_in_executor`` sono patchate di **processo**.

    Le attraversa anche il gateway — snapshot, backup, notifier, cron, le route
    wiki. Il ponte deve restare inerte per loro: portare il contesto del turno
    dentro ogni salto in executor dell'host cambierebbe la semantica di tutta
    l'applicazione, cosa che nessuno ha chiesto, e pagherebbe una copia per
    niente. Il gate è "su questo thread sta girando codice del modello,
    guardato", cioè le regole di import — l'unica delle due metà che
    ``_enter_guard`` mette in *entrambe* le modalità di ``restrict_to_workspace``.

    Test debole per costruzione: guarda l'identità dell'oggetto, non un effetto.
    Ma prima di questo, togliere il gate non faceva cadere niente in tutta la
    suite — misurato — e una scelta di perimetro che nessun test difende è una
    scelta che il prossimo refactoring disfa senza accorgersene.
    """

    def test_a_host_callable_crosses_untouched(self):
        from jenny.agent.tools.python_exec import _carry_guard_state

        def fn() -> None:
            pass

        assert _carry_guard_state(fn) is fn

    def test_a_guarded_callable_gets_the_bridge(self, workspace):
        """Controprova: con un guard attivo sul thread, la callable è avvolta."""
        from jenny.agent.tools.python_exec import _carry_guard_state, _import_guard_state

        def fn() -> None:
            pass

        previous = getattr(_import_guard_state, "rules", None)
        _import_guard_state.rules = (frozenset(), frozenset())
        try:
            assert _carry_guard_state(fn) is not fn
        finally:
            _import_guard_state.rules = previous


class TestOnlyOneBridgeExists:
    """Che il ponte sia **uno** è strutturale, quindi si controlla nel sorgente.

    **Onestà su quanto vale.** Le prime due asserzioni sono invarianti veri: le
    quattro primitive di trasporto (``_guard_state_snapshot`` /
    ``_apply_guard_state`` / ``_capture_snapshot`` / ``_apply_capture``) e
    ``contextvars.copy_context`` compaiono **solo** dentro
    ``_carry_turn_across_thread``, quindi una seconda copia del contesto o una
    seconda installazione dello stato thread-local **non può** esistere senza
    farlo cadere. Quello è il difetto che T4.13 chiude: due meccanismi di
    trasporto, ognuno con metà del lavoro.

    La terza è debole e va detto: elenca i costrutti di salto e pretende che
    ognuno sia dichiarato. Prova che una **riga** esiste, non che il ponte venga
    usato. Un salto nuovo che chiama ``_carry_turn_across_thread`` e poi ignora
    il valore di ritorno passa questo test e apre il buco tale e quale; e un
    ``threading.Thread`` raggiunto attraverso un alias che l'AST non riconosce
    non lo vede nemmeno. La rete vera sono i test comportamentali qui sopra —
    questo serve a fermarsi un attimo quando si scrive un salto, non a
    garantirlo.
    """

    @staticmethod
    def _tree(module):
        import ast
        import inspect

        return ast.parse(inspect.getsource(module)), module.__name__

    @staticmethod
    def _enclosing(tree, lineno: int) -> frozenset[str]:
        """**Tutte** le funzioni che contengono *lineno*, non la più interna.

        Il ponte è una funzione che ne contiene un'altra (``_carried``): guardare
        solo la più interna direbbe che il trasporto vive in ``_carried`` e non in
        ``_carry_turn_across_thread``, che è vero e inutile.
        """
        import ast

        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno):
                    names.add(node.name)
        return frozenset(names) or frozenset({"<module>"})

    def test_the_transport_primitives_live_in_one_function_only(self):
        """Le quattro primitive di trasporto, più la copia del contesto."""
        import ast

        from jenny.agent.tools import exec_session, python_exec

        watched = {
            "_guard_state_snapshot",
            "_apply_guard_state",
            "_capture_snapshot",
            "_apply_capture",
            "copy_context",
        }
        offenders = []
        for module in (python_exec, exec_session):
            tree, name = self._tree(module)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if called not in watched:
                    continue
                where = self._enclosing(tree, node.lineno)
                if not where & {"_carry_turn_across_thread", called}:
                    offenders.append(
                        f"{name}:{node.lineno} {called}() dentro {sorted(where)}"
                    )
        assert offenders == [], (
            "il trasporto di un turno attraverso un thread deve stare in "
            "_carry_turn_across_thread e in nessun altro posto — due meccanismi di "
            "trasporto è esattamente il difetto di T4.13, dove uno portava il "
            "thread-local e l'altro il ContextVar:\n  " + "\n  ".join(offenders)
        )

    def test_every_thread_hop_in_this_stack_is_declared(self):
        """L'elenco dei salti. Debole: v. il docstring della classe."""
        import ast

        from jenny.agent.tools import exec_session, python_exec

        # (modulo, funzione che contiene il salto) -> perché è a posto.
        declared = {
            ("jenny.agent.tools.python_exec", "_patch_asyncio_thread_hops"): (
                "è il patch stesso: avvolge la callable in _carry_guard_state, "
                "che è il ponte più il gate per il codice host"
            ),
            ("jenny.agent.tools.python_exec", "run_python_async"): (
                "passa _run_carried, costruito con _carry_turn_across_thread sul "
                "thread dell'event loop"
            ),
            ("jenny.agent.tools.exec_session", "__init__"): (
                "il thread grezzo della sessione: il ponte lo costruisce "
                "_ContextBoundNamespace, prima, dove il turno esiste ancora"
            ),
        }
        hops = {"run_in_executor", "to_thread", "Thread"}
        found = {}
        for module in (python_exec, exec_session):
            tree, name = self._tree(module)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if called in hops:
                    where = self._enclosing(tree, node.lineno)
                    key = next(
                        (
                            (name, fn)
                            for fn in sorted(where)
                            if (name, fn) in declared
                        ),
                        (name, "/".join(sorted(where))),
                    )
                    found[key] = called

        undeclared = sorted(k for k in found if k not in declared)
        assert undeclared == [], (
            "un salto di thread nuovo in questo stack: deve passare da "
            "_carry_turn_across_thread (entrambe le metà del turno, non una) e poi "
            "essere dichiarato qui con il perché — "
            f"{undeclared}"
        )
