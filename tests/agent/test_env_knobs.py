"""Fase 0.5 (parte AgentLoop) — Caratterizzazione: knob runtime da env-var.

Baseline (pre Fase 3.2): `JENNY_MAX_CONCURRENT_REQUESTS` è letto direttamente
da `os.environ` in `AgentLoop.__init__` (loop.py:297), bypassando `Config`.
Fase 3.2 lo sposterà su un campo tipizzato di `Config`.

Sta in tests/agent/ perché usa la fixture `loop_factory` (tests/agent/conftest.py).
"""

from __future__ import annotations

import asyncio


def test_concurrency_gate_defaults_to_semaphore(loop_factory, monkeypatch) -> None:
    monkeypatch.delenv("JENNY_MAX_CONCURRENT_REQUESTS", raising=False)
    loop = loop_factory()
    assert isinstance(loop._concurrency_gate, asyncio.Semaphore)


def test_concurrency_gate_env_zero_disables(loop_factory, monkeypatch) -> None:
    monkeypatch.setenv("JENNY_MAX_CONCURRENT_REQUESTS", "0")
    loop = loop_factory()
    # BASELINE: <=0 significa "illimitato" → nessun semaforo.
    assert loop._concurrency_gate is None
