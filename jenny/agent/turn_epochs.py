"""Epoch di turno per-sessione: rende sicuro abbandonare un turno bloccato.

Un task di turno cancellato può non morire mai (bloccato in un thread non
cancellabile, es. ``run_in_executor``). Aspettarlo congela l'intake loop;
abbandonarlo senza protezioni lo lascia libero di riprendere a scrivere
(stream delta, checkpoint, history, outbound) quando il thread finisce.

Il contratto: ogni turno riceve all'avvio un :class:`TurnToken` con l'epoch
corrente della sua session key. ``/stop`` e ``/new`` incrementano l'epoch
(*bump*), "ripudiando" i turni in volo: ogni punto in cui un turno rientra nel
mondo condiviso verifica ``is_current(token)`` e scarta in silenzio se
ripudiato. Così l'abbandono diventa una garanzia semantica ("quel turno non ha
più effetti"), non una speranza.

``TurnToken.epoch`` è mutabile di proposito: un comando che bumpa la propria
stessa sessione (``/new`` dentro il proprio turno) ri-adotta il token al nuovo
epoch e sopravvive al proprio bump.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TurnToken:
    """Identità di un turno: session key + epoch al momento dell'avvio."""

    key: str
    epoch: int


class TurnEpochs:
    """Registro degli epoch per session key (default 0, mai persistito)."""

    def __init__(self) -> None:
        self._epochs: dict[str, int] = {}

    def current(self, key: str) -> int:
        """Epoch corrente per *key* (0 se mai bumpata)."""
        return self._epochs.get(key, 0)

    def issue(self, key: str) -> TurnToken:
        """Emette il token per un turno che parte ora su *key*."""
        return TurnToken(key=key, epoch=self.current(key))

    def is_current(self, token: TurnToken | None) -> bool:
        """True se il turno del token non è stato ripudiato.

        ``None`` è sempre corrente: i chiamanti non governati (process_direct,
        test legacy) restano trasparenti al meccanismo.
        """
        if token is None:
            return True
        return token.epoch == self.current(token.key)

    def bump(self, key: str) -> int:
        """Incrementa l'epoch di *key*, ripudiando i turni in volo. Ritorna il nuovo epoch."""
        new_epoch = self.current(key) + 1
        self._epochs[key] = new_epoch
        return new_epoch
