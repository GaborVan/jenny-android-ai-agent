"""Pydantic-compatible wrapper implemented with stdlib dataclasses."""

from jenny.pydantic_compat.core import BaseModel, BaseSettings
from jenny.pydantic_compat.errors import ValidationError
from jenny.pydantic_compat.fields import (
    AliasChoices,
    ConfigDict,
    Field,
    FieldInfo,
    to_camel,
)
from jenny.pydantic_compat.validators import field_validator, model_validator

__all__ = [
    "AliasChoices",
    "BaseModel",
    "BaseSettings",
    "ConfigDict",
    "Field",
    "FieldInfo",
    "ValidationError",
    "field_validator",
    "model_validator",
    "to_camel",
]
