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
