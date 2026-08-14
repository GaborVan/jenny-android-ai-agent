"""Contabilità dei token per il wire-format Anthropic (modulo "leaf").

Casa unica della normalizzazione ``usage`` Anthropic → forma interna. Viveva
dentro il solo parse non-streaming, e per questo i due path hanno derivato: il
path streaming si costruiva il suo dizionario a mano, senza le cifre di cache.

Sull'onda Anthropic il conteggio arriva in **due** eventi: ``message_start``
porta gli input token e le voci di cache, ``message_delta`` porta gli output
token man mano. Chi legge solo il secondo riporta ``prompt_tokens`` a zero.

Leaf-level: solo stdlib.
"""

from __future__ import annotations

from typing import Any

__all__ = ["merge_raw_usage", "normalize_usage"]


def merge_raw_usage(target: dict[str, Any], incoming: Any) -> None:
    """Fonde un blocco ``usage`` grezzo in *target*, in-place.

    Un valore a zero non sovrascrive un valore già noto: alcuni gateway
    ripetono l'intero blocco a ogni delta azzerando i campi che non stanno
    aggiornando, e prenderli alla lettera cancellerebbe gli input token
    annunciati in ``message_start``. Un valore non-zero vince sempre, così gli
    output token cumulativi avanzano fino all'ultimo delta.
    """
    if not isinstance(incoming, dict):
        return
    for key, value in incoming.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value or key not in target:
            target[key] = value


def normalize_usage(usage_obj: Any) -> dict[str, int]:
    """Traduce lo ``usage`` Anthropic nella forma interna.

    ``prompt_tokens`` somma input, cache-creation e cache-read: sono tutti
    token di prompt, e tenerli separati farebbe sembrare un prompt in cache
    molto più piccolo di quello che è. Le voci di cache restano anche a sé,
    perché è da quelle che si vede se il caching sta funzionando.
    """
    # Blocco assente ≠ blocco a zero: senza conteggi il runner stima
    # (``usage_or_estimate`` scarta comunque i totali nulli), mentre una manciata
    # di zeri sarebbe un dato riportato che non abbiamo mai ricevuto.
    if not isinstance(usage_obj, dict) or not usage_obj:
        return {}

    input_tokens = int(usage_obj.get("input_tokens") or 0)
    output_tokens = int(usage_obj.get("output_tokens") or 0)
    cache_creation = int(usage_obj.get("cache_creation_input_tokens") or 0)
    cache_read = int(usage_obj.get("cache_read_input_tokens") or 0)
    total_prompt_tokens = input_tokens + cache_creation + cache_read

    usage: dict[str, int] = {
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_prompt_tokens + output_tokens,
    }
    for attr in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        value = usage_obj.get(attr)
        if value:
            usage[attr] = int(value)
    if cache_read:
        usage["cached_tokens"] = cache_read
    return usage
