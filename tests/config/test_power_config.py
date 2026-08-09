"""Test per ``config.power`` (PowerConfig): default, alias camelCase e la
regola per cui un ``keepAwake`` scritto male non deve impedire l'avvio."""

from __future__ import annotations

import pytest

from jenny.config.schema import Config, PowerConfig
from jenny.pydantic_compat import ValidationError


def test_power_config_defaults() -> None:
    cfg = PowerConfig()

    assert cfg.keep_awake == "turns"
    assert cfg.wakelock_rotate_min == 50
    assert cfg.watchdog_enabled is True
    assert cfg.watchdog_interval_min == 15
    assert cfg.alarm_driven_cron is True
    assert cfg.alarm_clock_fallback is True
    assert cfg.gap_warning_min == 60


def test_root_config_carries_power() -> None:
    config = Config()

    assert isinstance(config.power, PowerConfig)
    assert config.power.keep_awake == "turns"


def test_root_config_reads_nested_power_section() -> None:
    config = Config(**{"power": {"keepAwake": "always", "watchdogEnabled": False}})

    assert config.power.keep_awake == "always"
    assert config.power.watchdog_enabled is False


def test_power_config_reads_camel_case_input() -> None:
    cfg = PowerConfig(
        **{
            "keepAwake": "always",
            "wakelockRotateMin": 30,
            "watchdogEnabled": False,
            "watchdogIntervalMin": 20,
            "alarmDrivenCron": False,
            "alarmClockFallback": False,
            "gapWarningMin": 90,
        }
    )

    assert cfg.keep_awake == "always"
    assert cfg.wakelock_rotate_min == 30
    assert cfg.watchdog_enabled is False
    assert cfg.watchdog_interval_min == 20
    assert cfg.alarm_driven_cron is False
    assert cfg.alarm_clock_fallback is False
    assert cfg.gap_warning_min == 90


def test_power_config_reads_snake_case_input() -> None:
    cfg = PowerConfig(**{"keep_awake": "off", "wakelock_rotate_min": 0})

    assert cfg.keep_awake == "off"
    assert cfg.wakelock_rotate_min == 0


def test_power_config_dump_uses_camel_case_aliases() -> None:
    dumped = PowerConfig(keep_awake="off", gap_warning_min=120).model_dump(by_alias=True)

    assert dumped["keepAwake"] == "off"
    assert dumped["wakelockRotateMin"] == 50
    assert dumped["watchdogEnabled"] is True
    assert dumped["watchdogIntervalMin"] == 15
    assert dumped["alarmDrivenCron"] is True
    assert dumped["alarmClockFallback"] is True
    assert dumped["gapWarningMin"] == 120


def test_power_config_round_trips_through_camel_case() -> None:
    original = PowerConfig(keep_awake="always", wakelock_rotate_min=0, gap_warning_min=15)

    restored = PowerConfig(**original.model_dump(by_alias=True))

    assert restored == original


@pytest.mark.parametrize("mode", ["off", "turns", "always"])
def test_power_config_accepts_every_documented_mode(mode: str) -> None:
    assert PowerConfig(keep_awake=mode).keep_awake == mode


@pytest.mark.parametrize("raw", ["nope", "", "ON", "sempre", True, None, 3, []])
def test_invalid_keep_awake_falls_back_instead_of_raising(raw: object) -> None:
    # Un valore illeggibile è un refuso, non un motivo per non far partire il
    # gateway: si ricade su "turns", anche quando il tipo è tutto sbagliato.
    assert PowerConfig(**{"keepAwake": raw}).keep_awake == "turns"


@pytest.mark.parametrize("raw", ["Turns ", " ALWAYS", "Off"])
def test_dirty_but_valid_keep_awake_is_normalized_not_discarded(raw: str) -> None:
    assert PowerConfig(**{"keepAwake": raw}).keep_awake == raw.strip().lower()


def test_invalid_keep_awake_falls_back_on_snake_case_key_too() -> None:
    assert PowerConfig(**{"keep_awake": "sleepy"}).keep_awake == "turns"


def test_invalid_keep_awake_does_not_brick_the_root_config() -> None:
    config = Config(**{"power": {"keepAwake": 42}})

    assert config.power.keep_awake == "turns"


@pytest.mark.parametrize(
    "payload",
    [
        {"wakelockRotateMin": -1},
        {"wakelockRotateMin": 241},
        {"watchdogIntervalMin": 4},
        {"watchdogIntervalMin": 121},
        {"gapWarningMin": 4},
    ],
)
def test_out_of_range_values_are_rejected(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        PowerConfig(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"wakelockRotateMin": 0},
        {"wakelockRotateMin": 240},
        {"watchdogIntervalMin": 5},
        {"watchdogIntervalMin": 120},
        {"gapWarningMin": 5},
    ],
)
def test_range_boundaries_are_accepted(payload: dict[str, int]) -> None:
    assert PowerConfig(**payload) is not None
