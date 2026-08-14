"""Merge del corpo richiesta, condiviso fra i provider (modulo "leaf").

`deep_merge` serve a fondere l'``extra_body`` configurato dall'utente sopra i
default che il provider ha già calcolato, senza azzerare le chiavi vicine. Sta
qui e non fra gli helper OpenAI-compat perché la usano entrambi i provider, e
far importare l'uno dagli helper dell'altro sarebbe una dipendenza al rovescio.

Leaf-level: solo stdlib.
"""

from __future__ import annotations

from typing import Any

__all__ = ["deep_merge"]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *override* into *base*, returning a new dict.

    Nested dicts are merged key-by-key; all other types in *override*
    replace the corresponding key in *base*.
    """
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
