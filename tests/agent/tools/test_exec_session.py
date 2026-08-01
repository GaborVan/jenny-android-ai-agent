"""Tests that _PythonSession/ExecSessionManager actually stop background
threads, not just flip bookkeeping flags.

See the HIGH-severity audit finding: stop()/terminate()/idle-timeout/
_cleanup_python() all used to be cosmetic -- they flipped `_done`/`_timed_out`
but never signalled (or the signal was never observed by) the background
thread, so it kept running/consuming CPU indefinitely. These tests assert on
the real OS thread object (`session._thread.is_alive()` after a bounded
`join()`), not on the bookkeeping flags.
"""

import sys
import threading
import time

import pytest

from jenny.agent.tools.exec_session import ExecSessionManager, format_session_poll
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
    time.sleep(1.2)
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
    """Regression test for the process-global sys.stdout/sys.stderr redirect
    race: PythonNamespace.execute()/call_function() and
    _PythonSession._run() both use contextlib.redirect_stdout/redirect_stderr,
    which mutate the process-wide sys.stdout/sys.stderr attribute for the
    duration of the call. Real execution happens on separate OS threads (a
    dedicated background thread per exec session; a threadpool executor
    thread per one-shot python_exec call in production), so a long-running
    session and a concurrent one-shot call could otherwise transiently swap
    each other's capture buffer and misattribute or lose output.

    This exercises genuine overlap: a session prints periodically over
    ~300ms while a one-shot execute() call starts partway through and must
    wait (serialized by `_stdout_redirect_lock`) rather than racing it.
    """
    from jenny.agent.tools.exec_session import _PythonSession

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

    # The real process-wide sys.stdout/sys.stderr must be restored once both
    # finish -- the actual failure mode of the race is that a nested
    # redirect_stdout's __exit__ restores to the wrong prior value.
    assert sys.stdout is real_stdout
    assert sys.stderr is real_stderr

    # The one-shot call must have actually waited for the session's redirect
    # window to release (proving the two executions genuinely overlapped in
    # time rather than happening to run back-to-back anyway).
    assert oneshot_result["start"] - session.started_at < 0.1
    assert oneshot_result["end"] - oneshot_result["start"] > 0.15


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
