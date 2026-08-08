from jenny.config.schema import AgentDefaults, AtlasConfig


def test_atlas_config_defaults() -> None:
    cfg = AtlasConfig()

    assert cfg.enabled is True
    # Sei, non dodici: su Android il doze allunga i tick e il processo non
    # sopravvive sempre mezza giornata, quindi una scadenza a 12h rischiava
    # di non arrivare mai.
    assert cfg.interval_h == 6
    assert cfg.max_context_tokens == 1200


def test_atlas_config_builds_every_schedule_from_interval() -> None:
    # Non il default, altrimenti il test passerebbe anche ignorando il campo.
    cfg = AtlasConfig(interval_h=9)

    schedule = cfg.build_schedule()

    assert schedule.kind == "every"
    assert schedule.every_ms == 9 * 3_600_000
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
