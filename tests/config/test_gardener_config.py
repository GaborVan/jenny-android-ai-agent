"""Il knob del giardiniere (``config.agents.defaults.gardener``).

Tre numeri, e ognuno risponde a una domanda diversa: ogni quanto si *guarda*
(``interval_min``), da quanto la conversazione deve tacere prima di entrare
(``idle_min``), e quanto si aspetta prima di ritornare sulla stessa materia
(``min_hours_between_passes``). Confonderli è facile e le conseguenze sono
opposte, quindi il test li tiene distinti uno per uno.
"""

from __future__ import annotations

import pytest

from jenny.config.schema import AgentDefaults, GardenerConfig


def test_defaults() -> None:
    cfg = GardenerConfig()

    # Acceso come Dream e Atlas: senza righe di diario nuove il tick esce prima
    # di qualunque chiamata al provider, quindi su un'installazione che non usa i
    # progetti costa zero.
    assert cfg.enabled is True
    assert cfg.interval_min == 30
    assert cfg.idle_min == 30
    # Sei ore, e non è lo stesso numero di ``interval_min`` per caso: guardare
    # spesso costa nulla, *tornare* spesso sulla stessa materia è il degrado.
    assert cfg.min_hours_between_passes == 6


def test_the_schedule_comes_from_the_interval_not_from_the_other_two() -> None:
    # Valori tutti diversi e nessuno il default: così il test cade se il codice
    # costruisce lo schedule dal knob sbagliato.
    cfg = GardenerConfig(interval_min=7, idle_min=45, min_hours_between_passes=9)

    schedule = cfg.build_schedule()

    assert schedule.kind == "every"
    assert schedule.every_ms == 7 * 60_000


def test_the_description_names_all_three_numbers() -> None:
    """Va nei log all'avvio, ed è il solo posto dove si vede come è configurato:
    una descrizione che ne cita uno solo nasconde gli altri due."""
    text = GardenerConfig(
        interval_min=7, idle_min=45, min_hours_between_passes=9
    ).describe_schedule()

    assert "7min" in text and "45min" in text and "9h" in text


def test_dump_uses_camel_case_aliases() -> None:
    dumped = GardenerConfig(
        interval_min=15, idle_min=20, min_hours_between_passes=3
    ).model_dump(by_alias=True)

    assert dumped["intervalMin"] == 15
    assert dumped["idleMin"] == 20
    assert dumped["minHoursBetweenPasses"] == 3


def test_reads_camel_case_input() -> None:
    cfg = GardenerConfig(**{"intervalMin": 12, "idleMin": 8, "minHoursBetweenPasses": 4})

    assert (cfg.interval_min, cfg.idle_min, cfg.min_hours_between_passes) == (12, 8, 4)


def test_a_tick_of_zero_minutes_is_refused() -> None:
    """Zero significherebbe un tick continuo. I due orologi *possono* essere
    zero — spegnerli è una scelta legittima — ma il battito no."""
    with pytest.raises(Exception):
        GardenerConfig(interval_min=0)


@pytest.mark.parametrize("field", ["idle_min", "min_hours_between_passes"])
def test_the_two_clocks_can_be_switched_off(field: str) -> None:
    cfg = GardenerConfig(**{field: 0})

    assert getattr(cfg, field) == 0


def test_it_hangs_off_the_agent_defaults() -> None:
    assert isinstance(AgentDefaults().gardener, GardenerConfig)
