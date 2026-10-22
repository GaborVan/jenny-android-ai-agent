"""Lettura di ``GET /v1/models``: dagli id ai nomi che la UI può offrire.

Il bridge di casa espone i CLI come modelli (``codex``/``claude``/``gemini``), ma
il payload non è sempre lo stesso: l'API OpenAI mette gli id in ``data[].id``,
alcuni server in ``models[].name``, altri ancora restituiscono una lista nuda.
Chi legge deve accettarli tutti e non deve offrire due volte lo stesso modello.

Modulo foglia: solo stdlib. La parte WebUI che serve la lista modelli delega
qui, così la forma del payload si legge in un posto solo.
"""

from __future__ import annotations

from typing import Any

__all__ = ["model_id_from_row", "model_rows", "parse_model_ids"]


def model_id_from_row(row: Any) -> str | None:
    """L'id di un modello a partire da una riga, in qualunque forma sia.

    Una stringa è già l'id; un dizionario lo porta sotto ``id``/``name``/``model``
    (i tre nomi visti in natura). Qualunque altra cosa — un numero, una lista,
    un dizionario senza nessuna di quelle chiavi — non è un modello e vale
    ``None``, invece di una stringa inventata.
    """
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    for key in ("id", "name", "model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def model_rows(payload: Any) -> list[Any]:
    """Le righe grezze di un payload ``/v1/models``, se ne contiene una lista.

    L'API OpenAI incapsula in ``data``; alcuni server usano ``models``; altri
    rispondono con la lista nuda. Un dizionario che non ha né l'una né l'altra
    chiave non è una lista di modelli e vale ``[]``, non un errore: un payload
    inatteso si traduce in "nessun modello", che la UI sa già mostrare.
    """
    if isinstance(payload, dict):
        for key in ("data", "models"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return candidate
        return []
    if isinstance(payload, list):
        return payload
    return []


def parse_model_ids(payload: Any) -> list[str]:
    """Gli id dei modelli, nell'ordine del server e senza duplicati.

    L'ordine è quello che il server ha scelto (spesso già per rilevanza), e non
    va riordinato: la lista finisce in una tendina, e una tendina alfabetica
    perde l'unico suggerimento che il bridge dà su quale modello preferire.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for row in model_rows(payload):
        model_id = model_id_from_row(row)
        if model_id is None or model_id in seen:
            continue
        seen.add(model_id)
        ids.append(model_id)
    return ids
