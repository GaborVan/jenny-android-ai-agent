"""Validator decorators for the Pydantic compat layer."""

from __future__ import annotations

from typing import Any, Callable


def _unwrap_classmethod(fn: Any) -> Any:
    """Return the raw function wrapped by classmethod, if applicable."""
    return fn.__func__ if isinstance(fn, classmethod) else fn


def field_validator(*field_names: str) -> Callable[[Any], Any]:
    """Mark a classmethod as a validator for one or more fields."""
    def decorator(fn: Any) -> Any:
        target = _unwrap_classmethod(fn)
        target.__pydantic_field_validator__ = field_names
        return fn
    return decorator


def model_validator(*, mode: str) -> Callable[[Any], Any]:
    """Mark a method as a whole-model validator."""
    def decorator(fn: Any) -> Any:
        target = _unwrap_classmethod(fn)
        target.__pydantic_model_validator__ = mode
        return fn
    return decorator
