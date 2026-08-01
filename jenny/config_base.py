"""Shared Pydantic base model for configuration DTOs.

This module intentionally lives outside the ``jenny.config`` package so
runtime modules can define local config DTOs without importing the full root
configuration schema.
"""

from jenny.pydantic_compat import BaseModel, ConfigDict, to_camel


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
