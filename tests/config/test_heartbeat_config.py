import pytest

from jenny.config.schema import HeartbeatConfig
from jenny.pydantic_compat import ValidationError


def test_heartbeat_config_defaults_to_interval_seconds() -> None:
    cfg = HeartbeatConfig()

    assert cfg.interval_s == 30 * 60


def test_heartbeat_config_accepts_positive_interval() -> None:
    cfg = HeartbeatConfig(interval_s=60)

    assert cfg.interval_s == 60


def test_heartbeat_config_rejects_zero_interval() -> None:
    with pytest.raises(ValidationError):
        HeartbeatConfig(interval_s=0)


def test_heartbeat_config_rejects_negative_interval() -> None:
    with pytest.raises(ValidationError):
        HeartbeatConfig(interval_s=-30)
