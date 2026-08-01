import json

import pytest

from jenny.config.loader import (
    load_config,
    resolve_config_env_vars,
    save_config,
)
from jenny.pydantic_compat import BaseModel, Field


class _EnvModel(BaseModel):
    """Minimal pydantic model to exercise ``resolve_config_env_vars``."""

    text: str = ""
    mapping: dict = Field(default_factory=dict)
    items: list = Field(default_factory=list)
    count: int = 42
    flag: bool = True
    ratio: float = 3.14
    maybe: str | None = None


class TestResolveEnvVars:
    def test_replaces_string_value(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "hunter2")
        resolved = resolve_config_env_vars(_EnvModel(text="${MY_SECRET}"))
        assert resolved.text == "hunter2"

    def test_partial_replacement(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        resolved = resolve_config_env_vars(_EnvModel(text="https://${HOST}/api"))
        assert resolved.text == "https://example.com/api"

    def test_multiple_vars_in_one_string(self, monkeypatch):
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setenv("PASS", "secret")
        resolved = resolve_config_env_vars(_EnvModel(text="${USER}:${PASS}"))
        assert resolved.text == "alice:secret"

    def test_nested_dicts(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "abc123")
        model = _EnvModel(mapping={"outer": {"inner": {"token": "${TOKEN}"}}})
        resolved = resolve_config_env_vars(model)
        assert resolved.mapping["outer"]["inner"]["token"] == "abc123"

    def test_lists(self, monkeypatch):
        monkeypatch.setenv("VAL", "x")
        resolved = resolve_config_env_vars(_EnvModel(items=["${VAL}", "plain"]))
        assert resolved.items == ["x", "plain"]

    def test_ignores_non_strings(self):
        resolved = resolve_config_env_vars(_EnvModel())
        assert resolved.count == 42
        assert resolved.flag is True
        assert resolved.maybe is None
        assert resolved.ratio == 3.14

    def test_plain_strings_unchanged(self):
        model = _EnvModel(text="no vars here")
        resolved = resolve_config_env_vars(model)
        assert resolved.text == "no vars here"
        assert resolved is model

    def test_missing_var_raises(self, monkeypatch):
        monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
        with pytest.raises(ValueError, match="DOES_NOT_EXIST"):
            resolve_config_env_vars(_EnvModel(text="${DOES_NOT_EXIST}"))

    def test_nested_braces_raise(self, monkeypatch):
        """A malformed nested pattern must raise instead of silently mangling."""
        monkeypatch.setenv("INNER", "abc")
        with pytest.raises(ValueError, match="Malformed nested"):
            resolve_config_env_vars(_EnvModel(text="${OUTER${INNER}}"))

    def test_adjacent_vars_not_mistaken_for_nesting(self, monkeypatch):
        """Two separate, back-to-back references are valid, not nesting."""
        monkeypatch.setenv("VAR1", "foo")
        monkeypatch.setenv("VAR2", "bar")
        resolved = resolve_config_env_vars(_EnvModel(text="${VAR1}${VAR2}"))
        assert resolved.text == "foobar"


class TestResolveConfig:
    def test_resolves_env_vars_in_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "resolved-key")
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {"providers": {"providers": [{"name": "groq", "format": "openai_compat", "apiKey": "${TEST_API_KEY}"}]}}
            ),
            encoding="utf-8",
        )

        raw = load_config(config_path)
        assert raw.providers.providers[0].api_key == "${TEST_API_KEY}"

        resolved = resolve_config_env_vars(raw)
        assert resolved.providers.providers[0].api_key == "resolved-key"

    def test_save_preserves_templates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "real-token")
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {"websocket": {"token": "${MY_TOKEN}"}}
            ),
            encoding="utf-8",
        )

        raw = load_config(config_path)
        save_config(raw, config_path)

        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["websocket"]["token"] == "${MY_TOKEN}"

    def test_preserves_excluded_fields_when_no_env_refs(self, tmp_path):
        """Provider api_key with ``${VAR}`` must survive resolution."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {"providers": {"providers": [{"name": "anthropic", "format": "anthropic", "apiKey": "secret"}]}}
            ),
            encoding="utf-8",
        )

        raw = load_config(config_path)
        assert raw.providers.providers[0].api_key == "secret"

        resolved = resolve_config_env_vars(raw)
        assert resolved.providers.providers[0].api_key == "secret"

    def test_preserves_excluded_fields_with_env_refs(self, tmp_path, monkeypatch):
        """Api key env refs resolve correctly."""
        monkeypatch.setenv("TEST_API_KEY", "resolved-key")
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "providers": {"providers": [
                        {"name": "anthropic", "format": "anthropic", "apiKey": "secret"},
                        {"name": "groq", "format": "openai_compat", "apiKey": "${TEST_API_KEY}"},
                    ]}
                }
            ),
            encoding="utf-8",
        )

        raw = load_config(config_path)
        resolved = resolve_config_env_vars(raw)

        assert resolved.providers.providers[0].api_key == "secret"
        assert resolved.providers.providers[1].api_key == "resolved-key"
