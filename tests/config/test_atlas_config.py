from jenny.config.schema import AgentDefaults, AtlasConfig


def test_atlas_config_defaults() -> None:
    cfg = AtlasConfig()

    assert cfg.enabled is True
    assert cfg.interval_h == 12
    assert cfg.max_context_tokens == 1200


def test_atlas_config_builds_every_schedule_from_interval() -> None:
    cfg = AtlasConfig(interval_h=6)

    schedule = cfg.build_schedule()

    assert schedule.kind == "every"
    assert schedule.every_ms == 6 * 3_600_000
    assert schedule.expr is None


def test_atlas_config_describes_its_schedule() -> None:
    assert AtlasConfig(interval_h=24).describe_schedule() == "every 24h"


def test_atlas_config_dump_uses_camel_case_aliases() -> None:
    dumped = AtlasConfig(interval_h=5, max_context_tokens=900).model_dump(by_alias=True)

    assert dumped["intervalH"] == 5
    assert dumped["maxContextTokens"] == 900


def test_atlas_config_reads_camel_case_input() -> None:
    cfg = AtlasConfig(**{"intervalH": 8, "maxContextTokens": 700})

    assert cfg.interval_h == 8
    assert cfg.max_context_tokens == 700


def test_agent_defaults_carry_atlas() -> None:
    defaults = AgentDefaults()

    assert isinstance(defaults.atlas, AtlasConfig)
    assert defaults.atlas.enabled is True
