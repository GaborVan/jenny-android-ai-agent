"""Test del modello delle chiavi di sessione (unified + chiavi interne).

La conversazione utente è UNA sessione unificata (``unified:default``); il
lavoro interno (Dream, cron) usa chiavi separate via ``session_key_override``.
Questo contratto regge il routing di tutto il gateway: cambiare i valori
letterali romperebbe le sessioni già persistite su disco.
"""

from __future__ import annotations

import pytest

from jenny.session.keys import UNIFIED_SESSION_KEY, session_key_for_channel


def test_unified_key_literal_is_stable() -> None:
    """Il valore è persistito nei file di sessione: non deve cambiare mai."""
    assert UNIFIED_SESSION_KEY == "unified:default"


@pytest.mark.parametrize(
    ("channel", "chat_id"),
    [
        ("websocket", "default"),
        ("websocket", "altro-chat"),
        ("qualunque", "qualunque"),
        ("", ""),
    ],
)
def test_every_channel_chat_maps_to_unified(channel: str, chat_id: str) -> None:
    assert session_key_for_channel(channel, chat_id) == UNIFIED_SESSION_KEY


def test_dream_keys_never_collide_with_unified() -> None:
    """Le chiavi interne di Dream vivono in un namespace separato (``dream:``)."""
    from jenny.agent.memory import MemoryStore

    key = MemoryStore.dream_session_key()
    assert key.startswith("dream:")
    assert key != UNIFIED_SESSION_KEY
