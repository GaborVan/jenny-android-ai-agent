from jenny.config.schema import DreamConfig


def test_dream_config_defaults_to_interval_hours() -> None:
    cfg = DreamConfig()

    assert cfg.interval_h == 2


def test_dream_config_builds_every_schedule_from_interval() -> None:
    cfg = DreamConfig(interval_h=3)

    schedule = cfg.build_schedule()

    assert schedule.kind == "every"
    assert schedule.every_ms == 3 * 3_600_000
    assert schedule.expr is None


def test_dream_config_dump_uses_interval_h() -> None:
    cfg = DreamConfig(interval_h=5)

    dumped = cfg.model_dump(by_alias=True)

    assert dumped["intervalH"] == 5
