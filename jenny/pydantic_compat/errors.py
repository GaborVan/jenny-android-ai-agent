"""Validation error class for the Pydantic compat layer."""

from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when input data fails validation."""

    def __init__(
        self,
        message_or_errors: str | list[dict[str, Any]],
        *args: object,
        **kwargs: Any,
    ) -> None:
        if isinstance(message_or_errors, str):
            super().__init__(message_or_errors, *args, **kwargs)
            self.errors = [{"msg": message_or_errors, "type": "value_error"}]
        else:
            errors = list(message_or_errors)
            msg = "; ".join(str(e.get("msg", "")) for e in errors)
            super().__init__(msg, *args, **kwargs)
            self.errors = errors

