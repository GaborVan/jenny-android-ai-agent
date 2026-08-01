"""Configuration loading utilities."""

import json
import os
import re
from pathlib import Path
from typing import Any

from jenny.config.schema import Config
from jenny.pydantic_compat import BaseModel, ValidationError


def get_config_path() -> Path:
    """Get the configuration file path.

    Usa l'override ``RuntimeContext.config_path`` se impostato (usato dai test e
    da eventuali istanze multiple), altrimenti ``workspace/config.json``.
    """
    from jenny.runtime.context import get_runtime_context

    override = get_runtime_context().config_path
    if override:
        return override
    from jenny.config.paths import get_workspace_path
    return get_workspace_path() / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    config = Config()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            config = Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, ValidationError) as e:
            raise ValueError(f"Failed to load config from {path}: {e}") from e
    else:
        pass  # Config file doesn't exist; defaults from schema will be used

    _apply_ssrf_whitelist(config)
    _resolve_default_timezone(config)
    return config


def _resolve_default_timezone(config: Config) -> None:
    """Risolve la timezone "auto" (stringa vuota) in un valore concreto.

    Avviene una sola volta qui, nel funnel unico di caricamento: tutti i
    consumer a valle (container, cron, tool, settings) vedono sempre un nome
    concreto — la timezone del dispositivo se rilevata, altrimenti UTC.
    """
    if config.agents.defaults.timezone.strip():
        return
    from jenny.runtime.context import get_runtime_context

    config.agents.defaults.timezone = get_runtime_context().device_timezone or "UTC"


def _apply_ssrf_whitelist(config: Config) -> None:
    """Apply SSRF whitelist from config to the network security module."""
    from jenny.security.network import configure_ssrf_whitelist

    configure_ssrf_whitelist(config.security.ssrf_whitelist)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)
    _unresolve_default_timezone(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _unresolve_default_timezone(data: dict[str, Any]) -> None:
    """Riporta a "auto" la timezone risolta prima della persistenza.

    ``load_config`` risolve la sentinella vuota nella timezone del device;
    senza questo passo ogni salvataggio la congelerebbe come valore esplicito
    (e smetterebbe di seguire i cambi di timezone del dispositivo). Se il
    valore coincide con la timezone del device si riscrive ``""`` (= auto);
    una scelta esplicita diversa viene persistita normalmente.
    """
    from jenny.runtime.context import get_runtime_context

    device_tz = get_runtime_context().device_timezone
    if not device_tz:
        return
    defaults = data.get("agents", {}).get("defaults")
    if isinstance(defaults, dict) and defaults.get("timezone") == device_tz:
        defaults["timezone"] = ""


_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_config_env_vars(config: Config) -> Config:
    """Return *config* with ``${VAR}`` env-var references resolved.

    Walks in place so fields declared with ``exclude=True`` survive;
    returns the same instance when no references are present.
    Raises ``ValueError`` if a referenced variable is not set.
    """
    return _resolve_in_place(config)


def _resolve_in_place(obj: Any) -> Any:
    if isinstance(obj, str):
        _check_nested_env_refs(obj)
        new = _ENV_REF_PATTERN.sub(_env_replace, obj)
        return new if new != obj else obj
    if isinstance(obj, BaseModel):
        updates: dict[str, Any] = {}
        for name in type(obj).model_fields:
            old = getattr(obj, name)
            new = _resolve_in_place(old)
            if new is not old:
                updates[name] = new
        extras = obj.__pydantic_extra__
        new_extras: dict[str, Any] | None = None
        if extras:
            resolved = {k: _resolve_in_place(v) for k, v in extras.items()}
            if any(resolved[k] is not extras[k] for k in extras):
                new_extras = resolved
        if not updates and new_extras is None:
            return obj
        copy = obj.model_copy(update=updates) if updates else obj.model_copy()
        if new_extras is not None:
            copy.__pydantic_extra__ = new_extras
        return copy
    if isinstance(obj, dict):
        resolved = {k: _resolve_in_place(v) for k, v in obj.items()}
        return resolved if any(resolved[k] is not obj[k] for k in obj) else obj
    if isinstance(obj, list):
        resolved = [_resolve_in_place(v) for v in obj]
        return resolved if any(nv is not ov for nv, ov in zip(resolved, obj)) else obj
    return obj


def _check_nested_env_refs(value: str) -> None:
    """Reject a malformed/nested ``${...}`` pattern such as ``${OUTER${INNER}}``.

    ``_ENV_REF_PATTERN`` matches the *innermost* ``${...}`` span, so a nested
    reference like ``${OUTER${INNER}}`` would otherwise resolve only
    ``${INNER}`` and silently leave a mangled literal (e.g. ``${OUTERabc}``)
    in the config. Detect a second ``${`` opening before the matching ``}``
    of an already-open ``${`` and raise instead of producing that value.

    Back-to-back, non-nested references like ``${VAR1}${VAR2}`` are valid
    (each ``${`` is closed before the next one opens) and are not affected.
    """
    depth = 0
    i = 0
    n = len(value)
    while i < n:
        if value[i] == "$" and i + 1 < n and value[i + 1] == "{":
            if depth > 0:
                raise ValueError(
                    "Malformed nested '${...}' reference in config value: "
                    f"{value!r}"
                )
            depth = 1
            i += 2
            continue
        if value[i] == "}" and depth > 0:
            depth = 0
        i += 1


def _env_replace(match: re.Match[str]) -> str:
    name = match.group(1)
    value = os.environ.get(name)
    if value is None:
        raise ValueError(
            f"Environment variable '{name}' referenced in config is not set"
        )
    return value
