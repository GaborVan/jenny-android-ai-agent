"""Tests that _PythonSession/ExecSessionManager actually stop background
threads, not just flip bookkeeping flags.

See the HIGH-severity audit finding: stop()/terminate()/idle-timeout/
_cleanup_python() all used to be cosmetic -- they flipped `_done`/`_timed_out`
but never signalled (or the signal was never observed by) the background
thread, so it kept running/consuming CPU indefinitely. These tests assert on
the real OS thread object (`session._thread.is_alive()` after a bounded
`join()`), not on the bookkeeping flags.
"""

import asyncio
import sys
import threading
import time

import pytest

from jenny.agent.tools.exec_session import (
    ExecSessionManager,
    _PythonSession,
    format_result_line,
    format_session_poll,
)
from jenny.agent.tools.python_exec import PythonNamespace

# A loop with no natural end that periodically hits a Python-level line
# event (via a call into time.sleep), so the trace-based checkpoint
# installed in _run() gets a chance to observe the stop signal quickly.
LOOPING_CODE = (
    "import time\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    time.sleep(0.005)\n"
)


def _make_manager() -> ExecSessionManager:
    return ExecSessionManager(max_sessions=8, idle_timeout=1800)


@pytest.mark.asyncio
async def test_explicit_terminate_stops_the_background_thread():
    """write_stdin(terminate=true)'s underlying call (session.terminate())
    must actually kill the background thread, not just mark it done."""
    manager = _make_manager()
    namespace = PythonNamespace()

    session_id, poll = await manager.start_python(
        code=LOOPING_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=namespace,
        timeout=None,
        yield_time_ms=50,
        max_output_chars=10_000,
    )
    assert not poll.done

    # Grab the real session object/thread before it gets torn down.
    session = manager._python_sessions[session_id]
    thread = session._thread
    assert thread.is_alive()

    # Mirrors write_stdin(terminate=True): poll_python(terminate=True) calls
    # session.terminate() internally.
    await manager.poll_python(
        session_id=session_id,
        yield_time_ms=0,
        max_output_chars=10_000,
        terminate=True,
    )

    thread.join(timeout=2.0)
    assert not thread.is_alive(), "background thread kept running after terminate()"


@pytest.mark.asyncio
async def test_user_terminate_is_not_reported_as_timeout():
    """Una terminazione volontaria (write_stdin terminate=true) deve essere
    riportata come 'Session terminated.', non mascherata da errore di timeout:
    poll.terminated True, poll.timed_out False, e format_session_poll non deve
    emettere il messaggio di timeout."""
    manager = _make_manager()
    namespace = PythonNamespace()

    session_id, _ = await manager.start_python(
        code=LOOPING_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=namespace,
        timeout=None,  # nessuna deadline: solo terminazione utente
        yield_time_ms=50,
        max_output_chars=10_000,
    )

    poll = await manager.poll_python(
        session_id=session_id,
        yield_time_ms=0,
        max_output_chars=10_000,
        terminate=True,
    )

    assert poll.terminated is True
    assert poll.timed_out is False

    rendered = format_session_poll(session_id, poll)
    assert "Session terminated." in rendered
    assert "timed out" not in rendered


@pytest.mark.asyncio
async def test_idle_timeout_deadline_stops_the_background_thread():
    """Hitting the per-session execution deadline inside poll() must signal
    the thread to stop, not just flip _timed_out."""
    manager = _make_manager()
    namespace = PythonNamespace()

    session_id, poll = await manager.start_python(
        code=LOOPING_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=namespace,
        timeout=1,  # 1 second execution deadline
        yield_time_ms=0,
        max_output_chars=10_000,
    )
    assert not poll.done
    session = manager._python_sessions[session_id]
    thread = session._thread

    # Wait past the deadline, then poll again -- poll() itself must notice
    # the deadline has passed and call stop().
    #
    # ``await asyncio.sleep`` and not ``time.sleep``: the work runs on a
    # background thread either way, so the wall time is the same, but a blocking
    # sleep inside an async test parks the loop as well. Nothing else needs it
    # here -- and that is the point: it needs to stay that way by accident for
    # this to keep working.
    await asyncio.sleep(1.2)
    poll2 = await manager.poll_python(
        session_id=session_id,
        yield_time_ms=0,
        max_output_chars=10_000,
    )
    assert poll2.timed_out
    assert poll2.terminated is False  # deadline != terminazione volontaria

    thread.join(timeout=2.0)
    assert not thread.is_alive(), "background thread kept running past its deadline"


@pytest.mark.asyncio
async def test_cleanup_python_stops_stale_session_thread():
    """_cleanup_python() must signal stale sessions to stop before dropping
    them from the tracking dict, instead of orphaning a live thread."""
    manager = _make_manager()
    namespace = PythonNamespace()

    session_id, poll = await manager.start_python(
        code=LOOPING_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=namespace,
        timeout=None,
        yield_time_ms=50,
        max_output_chars=10_000,
    )
    assert not poll.done
    session = manager._python_sessions[session_id]
    thread = session._thread
    assert thread.is_alive()

    # Simulate the session having gone idle long enough to be swept.
    session.last_access -= manager.idle_timeout + 1

    manager._cleanup_python()
    assert session_id not in manager._python_sessions

    thread.join(timeout=2.0)
    assert not thread.is_alive(), "cleanup dropped tracking but left the thread running"


SLOW_PRINTING_SESSION_CODE = (
    "import time\n"
    "for i in range(6):\n"
    "    print(f'SESSION-{i}')\n"
    "    time.sleep(0.05)\n"
)


def test_concurrent_session_and_oneshot_exec_do_not_cross_contaminate_stdout():
    """Two concurrent executions must not steal each other's output.

    Storicamente questa era la regressione del redirect GLOBALE:
    ``PythonNamespace.execute()``/``call_function()`` e ``_PythonSession._run()``
    usavano ``contextlib.redirect_stdout``/``redirect_stderr``, che mutano il
    ``sys.stdout``/``sys.stderr`` di PROCESSO per la durata della chiamata.
    L'esecuzione vera avviene su thread separati (un thread dedicato per exec
    session; un worker del pool di python_exec per ogni chiamata one-shot),
    quindi due esecuzioni sovrapposte potevano scambiarsi il buffer di cattura.
    La cura era un lock di processo tenuto per tutta la finestra guardata: ha
    risolto l'attribuzione e ha creato R9 (un thread parcheggiato su
    ``lock.acquire()`` non è interrompibile).

    Ora la cattura è PER THREAD (``python_exec._ThreadRoutedStream``), quindi
    l'invariante da tenere ferma è più forte di prima: nessuna
    contaminazione **e** nessuna serializzazione. L'ultima asserzione è quella
    che cambia segno — il one-shot NON deve più aspettare la sessione.
    """
    from jenny.agent.tools.exec_session import _PythonSession
    from jenny.agent.tools.python_exec import _ThreadRoutedStream

    real_stdout = sys.stdout
    real_stderr = sys.stderr

    session_namespace = PythonNamespace()
    session = _PythonSession(
        session_id="test-session",
        code=SLOW_PRINTING_SESSION_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=session_namespace,
        timeout=None,
    )

    # Give the session a moment to actually be inside its redirect window
    # before starting the "concurrent" one-shot call.
    time.sleep(0.02)

    oneshot_namespace = PythonNamespace()
    oneshot_result: dict = {}

    def _run_oneshot():
        start = time.monotonic()
        stdout, stderr, _ = oneshot_namespace.execute(
            "print('ONESHOT-A'); print('ONESHOT-B')",
        )
        oneshot_result["stdout"] = stdout
        oneshot_result["stderr"] = stderr
        oneshot_result["start"] = start
        oneshot_result["end"] = time.monotonic()

    oneshot_thread = threading.Thread(target=_run_oneshot)
    oneshot_thread.start()
    oneshot_thread.join(timeout=5)
    assert not oneshot_thread.is_alive(), "one-shot execute() did not finish in time"

    session.join(timeout=5)
    assert not session._thread.is_alive(), "session did not finish in time"

    # Neither execution's captured output contains the other's prints.
    assert "ONESHOT-A" in oneshot_result["stdout"]
    assert "ONESHOT-B" in oneshot_result["stdout"]
    assert "SESSION-" not in oneshot_result["stdout"]
    assert not oneshot_result["stderr"]

    session_output = "".join(session._output_chunks)
    assert "SESSION-" in session_output
    assert "ONESHOT" not in session_output

    # Il thread che non sta catturando nulla continua a scrivere sullo stream
    # VERO: il proxy è trasparente, non un dirottamento globale. (Il proxy
    # resta montato — come ogni altro patch di python_exec — quindi si verifica
    # il bersaglio, non l'identità dell'oggetto.)
    for stream, real in ((sys.stdout, real_stdout), (sys.stderr, real_stderr)):
        if isinstance(stream, _ThreadRoutedStream):
            assert stream._jenny_target is real
        else:
            assert stream is real

    # Sovrapposizione genuina: il one-shot è partito mentre la sessione stava
    # ancora stampando...
    assert oneshot_result["start"] - session.started_at < 0.1
    # ...e NON ha dovuto aspettare che finisse. La sessione stampa per ~300ms;
    # prima della cattura per-thread il one-shot restava fermo sul lock per
    # tutto quel tempo.
    assert oneshot_result["end"] - oneshot_result["start"] < 0.15


@pytest.mark.asyncio
async def test_stop_event_does_not_break_normal_short_execution():
    """Sanity check: the new settrace-based checkpoint must not interfere
    with ordinary, quick code execution."""
    manager = _make_manager()
    namespace = PythonNamespace()

    session_id, poll = await manager.start_python(
        code="1 + 1",
        function=None,
        args=None,
        kwargs=None,
        namespace=namespace,
        timeout=None,
        yield_time_ms=200,
        max_output_chars=10_000,
    )
    assert poll.done
    assert poll.exit_code == 0
    assert "Result: 2" in poll.output
    assert session_id not in manager._python_sessions


def test_stop_is_reported_as_a_stop_not_as_a_traceback():
    """`_SessionStopped` deve risalire attraverso `PythonNamespace.execute()`.

    È una `BaseException` proprio perché nessun `except Exception` la ingoi;
    quando `execute()`/`call_function()` sono passate a `except BaseException`
    (confine del sandbox contro `SystemExit`) hanno però iniziato a ingoiare
    anche questa, rendendo morto l'`except _SessionStopped` di `_run()`: uno
    `/stop` stampava un traceback grezzo e chiudeva con exit code 0, cioè
    successo.
    """
    session = _PythonSession(
        session_id="stopped",
        code=LOOPING_CODE,
        function=None,
        args=None,
        kwargs=None,
        namespace=PythonNamespace(),
        timeout=None,
    )
    # Lascia partire il thread prima di chiedere lo stop, così l'interruzione
    # cade DENTRO l'exec e non sul controllo preliminare di _run().
    time.sleep(0.1)
    session.stop()
    session.join(timeout=5.0)

    assert not session._thread.is_alive()
    output = "".join(session._output_chunks)
    assert "Execution stopped (session was terminated)." in output
    assert "Traceback" not in output
    assert session._exit_code == -1


def _run_session_raising(raised: str) -> _PythonSession:
    session = _PythonSession(
        session_id=f"raised-{raised}",
        code=f"import asyncio\nraise {raised}",
        function=None,
        args=None,
        kwargs=None,
        namespace=PythonNamespace(),
        timeout=None,
    )
    session.join(timeout=5.0)
    assert not session._thread.is_alive()
    return session


def test_a_cancellederror_in_a_session_is_reported_as_a_failure():
    """``asyncio.CancelledError`` non deve passare per successo silenzioso.

    È nella tupla di carve-out di ``PythonNamespace.execute`` perché nel
    percorso one-shot c'è un consumatore (l'await di ``run_python_async``).
    Qui no: risaliva fino a uccidere il thread con un "Exception in thread"
    sullo stderr vero, e il ``finally`` di ``_run`` chiudeva con output vuoto
    ed exit code 0. Il modello leggeva "riuscito, niente da dire".
    """
    session = _run_session_raising("asyncio.CancelledError()")
    output = "".join(session._output_chunks)
    assert "Traceback" in output, f"nessun traceback: {output!r}"
    assert session._exit_code == 1


@pytest.mark.parametrize("raised", ["SystemExit(2)", "KeyboardInterrupt()"])
def test_a_systemexit_in_a_session_at_least_reaches_the_model(raised: str):
    """Il traceback arriva al modello; l'exit code resta 0, ed è un difetto noto.

    Queste due non risalgono fino a ``_run``: le ferma il confine del sandbox
    in ``PythonNamespace.execute``, che le rende testo su ``stderr_buf`` e
    ritorna normalmente. Quindi ``_run`` non vede nessuna eccezione e il
    ``finally`` chiude a 0, mentre il traceback è comunque nell'output.

    Distinguere "stderr non vuoto" da "il codice è esploso" richiede che
    ``execute()`` dica quale dei due è stato — un cambio di firma su un file
    che va maneggiato con calma. Il test inchioda il comportamento di oggi:
    se qualcuno lo corregge diventa rosso, ed è il momento giusto per
    aggiornarlo.
    """
    session = _run_session_raising(raised)
    output = "".join(session._output_chunks)
    assert "Traceback" in output, f"nessun traceback per {raised}: {output!r}"
    assert raised.split("(")[0] in output
    assert session._exit_code == 0


def test_format_result_line_renders_a_normal_repr():
    assert format_result_line([1, "a"]) == """Result: [1, 'a']"""


def test_format_result_line_contains_a_baseexception_from_repr():
    """Un ``__repr__`` che alza ``SystemExit`` non deve uscire dal sandbox.

    Il ``repr`` del risultato viene reso DOPO ``_exit_guard``, quindi fuori
    dall'``except BaseException`` di ``PythonNamespace.execute``: senza la
    protezione in ``format_result_line`` l'eccezione atterrerebbe sul future e
    asyncio la rilancerebbe fuori dall'event loop — lo stesso crash (B1) che
    quel confine esiste per evitare.
    """

    class _Hostile:
        def __repr__(self) -> str:
            raise SystemExit(2)

    line = format_result_line(_Hostile())
    assert line.startswith("Result: <repr() raised SystemExit")
    assert "_Hostile" in line
