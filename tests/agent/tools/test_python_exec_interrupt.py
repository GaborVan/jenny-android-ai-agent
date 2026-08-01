"""Interrupt best-effort del thread di python_exec (one-shot).

Su Android/Chaquopy non esiste un sottoprocesso Python killabile: il one-shot
gira in un thread del pool di default. ``PyThreadState_SetAsyncExc`` inietta
:class:`PythonExecInterrupted` nel thread su timeout o cancellazione (/stop),
fermando loop e codice Python puro; una chiamata C bloccante resta zombie ma
innocua (epoch di turno). Il thread del pool NON muore: torna idle, quindi
l'osservabile giusto è "il codice smette di girare", via contatore.
"""

from __future__ import annotations

import asyncio

import pytest

from jenny.agent.tools.python_exec import (
    PythonNamespace,
    _interrupt_thread,
    run_python_async,
)


def _make_namespace(tmp_path, counter: dict) -> PythonNamespace:
    ns = PythonNamespace(working_dir=str(tmp_path), workspace=str(tmp_path))
    ns.register_function("_tick", lambda: counter.__setitem__("n", counter["n"] + 1))
    return ns


async def _assert_loop_stopped(counter: dict) -> None:
    """Il loop interrotto deve smettere di incrementare il contatore."""
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        before = counter["n"]
        await asyncio.sleep(0.25)
        if counter["n"] == before:
            return
    pytest.fail(f"il loop gira ancora dopo l'interrupt (n={counter['n']})")


async def test_timeout_interrupts_pure_python_loop(tmp_path):
    counter = {"n": 0}
    ns = _make_namespace(tmp_path, counter)

    result = await run_python_async(
        code="while True:\n    _tick()",
        function=None, args=None, kwargs=None,
        namespace=ns, timeout=1, max_output_chars=1000,
    )

    assert "timed out" in result
    await _assert_loop_stopped(counter)


async def test_cancel_fires_interrupt(tmp_path):
    counter = {"n": 0}
    ns = _make_namespace(tmp_path, counter)

    task = asyncio.create_task(run_python_async(
        code="while True:\n    _tick()",
        function=None, args=None, kwargs=None,
        namespace=ns, timeout=None, max_output_chars=1000,
    ))
    # Attende che il loop nel thread stia davvero girando.
    while counter["n"] == 0:
        await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await _assert_loop_stopped(counter)


async def test_interrupt_noop_on_missing_or_stale_thread():
    _interrupt_thread(None)  # nessun ident catturato: no-op
    _interrupt_thread(2**31 - 1)  # ident inesistente: SetAsyncExc ritorna 0, no raise
