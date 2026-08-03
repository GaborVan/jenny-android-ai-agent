import json

import pytest

from jenny.config.loader import load_config


def test_load_config_missing_file_uses_defaults(tmp_path) -> None:
    config = load_config(tmp_path / "missing.json")

    assert config.agents.defaults.max_tokens == 16384
    assert config.agents.defaults.reasoning_effort == "medium"


def test_load_config_invalid_json_fails_fast(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{broken json", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to load config"):
        load_config(config_path)


def test_load_config_invalid_schema_fails_fast(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"tools": {"python_exec": {"timeout": -1}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Failed to load config"):
        load_config(config_path)
