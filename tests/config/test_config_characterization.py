"""Fase 0.5 — Caratterizzazione: knob runtime via env-var + risoluzione schema.

Baseline (pre Fase 3): diversi knob runtime sono letti DIRETTAMENTE da
`os.environ`, bypassando `Config` come fonte di verità. Fase 3.2 li sposterà su
campi tipizzati di `Config`; questi test fissano l'effetto attuale.

Coperti qui (pure/schema):
- `JENNY_STREAM_IDLE_TIMEOUT_S` via `resolve_stream_idle_timeout_s`
  (providers/base.py).
- Lo schema `Config` si istanzia coi sub-config dei tool risolti (la dance
  `_lazy_default`/`model_rebuild` che Fase 3.4 semplifica).

Il knob `JENNY_MAX_CONCURRENT_REQUESTS` (che richiede un AgentLoop, e quindi
la fixture `loop_factory` disponibile solo in tests/agent/) è caratterizzato in
`tests/agent/test_env_knobs.py`.
"""

from __future__ import annotations

from jenny.providers.base import (
    DEFAULT_STREAM_IDLE_TIMEOUT_S,
    MAX_STREAM_IDLE_TIMEOUT_S,
    resolve_stream_idle_timeout_s,
)


def test_stream_idle_timeout_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("JENNY_STREAM_IDLE_TIMEOUT_S", "12.5")
    assert resolve_stream_idle_timeout_s() == 12.5


def test_stream_idle_timeout_defaults_and_clamps() -> None:
    assert resolve_stream_idle_timeout_s(env_value=None) == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="") == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="-1") == DEFAULT_STREAM_IDLE_TIMEOUT_S
    assert resolve_stream_idle_timeout_s(env_value="999999") == MAX_STREAM_IDLE_TIMEOUT_S


def test_config_schema_resolves_tool_subconfigs() -> None:
    """La dance model_rebuild produce un Config con i sub-config dei tool risolti."""
    from jenny.config.schema import Config

    cfg = Config()
    assert cfg.tools is not None
    # I sub-config dei tool sono modelli concreti (non ForwardRef irrisolti).
    assert cfg.tools.model_dump() is not None
