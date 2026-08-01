"""Tipo di risultato tool strutturato.

Prima, il successo/fallimento di un tool era "stringly-typed": il registry
decideva con ``result.startswith("Error")``, mis-classificando qualsiasi output
legittimo che iniziasse per "Error". ``ToolResult`` rende successo ed errore
strutturalmente distinti.

Compatibilità: i tool NON ancora migrati continuano a ritornare ``str``/blocchi
e il registry applica la vecchia convenzione. Ciò che l'LLM vede resta invariato
(``render()`` ritorna il contenuto), quindi il comportamento del modello non
cambia; cambia solo la robustezza interna.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolError:
    """Errore tool machine-readable."""

    code: str
    message: str


@dataclass
class ToolResult:
    """Esito di un tool: successo (con contenuto) o errore (strutturato).

    ``content`` è ciò che va nello stream verso l'LLM: una stringa o una lista
    di blocchi di contenuto (stesso contratto di ``Tool.execute``).
    """

    ok: bool
    content: Any = ""
    error: ToolError | None = None

    @classmethod
    def success(cls, content: Any) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        code: str = "error",
        content: Any = None,
    ) -> "ToolResult":
        return cls(
            ok=False,
            content=content if content is not None else message,
            error=ToolError(code=code, message=message),
        )

    def render(self) -> Any:
        """Ritorna il contenuto visto dall'LLM (stringa o blocchi)."""
        return self.content
