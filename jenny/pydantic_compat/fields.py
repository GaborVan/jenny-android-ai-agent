"""Field definitions and configuration helpers for the Pydantic compat layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class _MissingType:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _MissingType()


class AliasChoices:
    """Accept any of the provided aliases for a field."""

    def __init__(self, *aliases: str) -> None:
        self.aliases = list(aliases)

    def __contains__(self, item: object) -> bool:
        return item in self.aliases

    def __repr__(self) -> str:
        return f"AliasChoices({', '.join(repr(a) for a in self.aliases)})"


@dataclass
class FieldInfo:
    """Metadata describing a model field."""

    name: str = ""
    annotation: Any = None
    default: Any = MISSING
    default_factory: Callable[[], Any] | None = None
    alias: str | None = None
    validation_alias: str | AliasChoices | None = None
    serialization_alias: str | None = None
    exclude: bool = False
    repr: bool = True
    ge: float | None = None
    le: float | None = None
    field_validators: list[Any] = field(default_factory=list)
    resolved_type: Any = None

    def get_default(self) -> Any:
        if self.default is not MISSING:
            return self.default
        if self.default_factory is not None:
            return self.default_factory()
        return MISSING


def Field(  # noqa: N802
    *,
    default: Any = MISSING,
    default_factory: Callable[[], Any] | None = None,
    ge: float | None = None,
    le: float | None = None,
    validation_alias: str | AliasChoices | None = None,
    serialization_alias: str | None = None,
    exclude: bool = False,
    repr: bool = True,
) -> FieldInfo:
    """Declare a field with validation metadata."""
    return FieldInfo(
        default=default,
        default_factory=default_factory,
        ge=ge,
        le=le,
        validation_alias=validation_alias,
        serialization_alias=serialization_alias,
        exclude=exclude,
        repr=repr,
    )


def ConfigDict(  # noqa: N802
    *,
    extra: str | None = None,
    alias_generator: Callable[[str], str] | None = None,
    populate_by_name: bool | None = None,
    **extra_kwargs: Any,
) -> dict[str, Any]:
    """Build a model_config dictionary."""
    result: dict[str, Any] = dict(extra_kwargs)
    if extra is not None:
        result["extra"] = extra
    if alias_generator is not None:
        result["alias_generator"] = alias_generator
    if populate_by_name is not None:
        result["populate_by_name"] = populate_by_name
    return result


def to_camel(snake: str) -> str:
    """Convert snake_case to camelCase."""
    parts = snake.split("_")
    if not parts:
        return snake
    return parts[0] + "".join(part.capitalize() for part in parts[1:])
