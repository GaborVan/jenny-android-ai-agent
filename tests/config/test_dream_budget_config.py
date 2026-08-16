"""Test per i budget della memoria lunga su ``DreamConfig``.

Il punto delicato non è la validazione in sé ma il *segno* del vincolo: qui
zero è il default di spedizione ("misura, non applicare"), non un valore
malformato. Questi test esistono soprattutto per impedire che qualcuno
stringa ``ge=0`` in ``gt=0`` durante una pulizia.
"""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config
from jenny.config.schema import DreamConfig
from jenny.pydantic_compat import ValidationError


def test_budget_knobs_default_to_measure_only() -> None:
    cfg = DreamConfig()

    # 0 = gauge sì, rifiuto no: i tetti si scelgono dopo aver letto le
    # dimensioni reali sul device, cambiando la config e non il codice.
    assert cfg.memory_budget_chars == 0
    assert cfg.user_budget_chars == 0
    assert cfg.soul_budget_chars == 0
    assert cfg.review_every_runs == 12


@pytest.mark.parametrize(
    "field",
    ["memory_budget_chars", "user_budget_chars", "soul_budget_chars"],
)
def test_zero_is_accepted_on_every_budget(field: str) -> None:
    # Questo è il test che protegge dal "fix" in ``gt=0``: zero non è un refuso,
    # è lo stato in cui la feature viene spedita — enforcement disattivato.
    assert getattr(DreamConfig(**{field: 0}), field) == 0


@pytest.mark.parametrize(
    "field",
    ["memory_budget_chars", "user_budget_chars", "soul_budget_chars"],
)
def test_negative_budgets_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        DreamConfig(**{field: -1})


def test_review_every_runs_rejects_zero_but_accepts_one() -> None:
    # Un review pass ogni zero run non è "disattivato", è un valore senza senso.
    with pytest.raises(ValidationError):
        DreamConfig(review_every_runs=0)

    assert DreamConfig(review_every_runs=1).review_every_runs == 1


def test_budget_knobs_dump_with_camel_case_aliases() -> None:
    dumped = DreamConfig(
        memory_budget_chars=5000,
        user_budget_chars=1500,
        soul_budget_chars=0,
        review_every_runs=6,
    ).model_dump(by_alias=True)

    assert dumped["memoryBudgetChars"] == 5000
    assert dumped["userBudgetChars"] == 1500
    assert dumped["soulBudgetChars"] == 0
    assert dumped["reviewEveryRuns"] == 6


def test_budget_knobs_read_camel_case_input() -> None:
    cfg = DreamConfig(
        **{
            "memoryBudgetChars": 4096,
            "userBudgetChars": 2048,
            "soulBudgetChars": 1024,
            "reviewEveryRuns": 24,
        }
    )

    assert cfg.memory_budget_chars == 4096
    assert cfg.user_budget_chars == 2048
    assert cfg.soul_budget_chars == 1024
    assert cfg.review_every_runs == 24


def test_budget_knobs_round_trip_through_camel_case() -> None:
    original = DreamConfig(memory_budget_chars=6000, review_every_runs=3)

    restored = DreamConfig(**original.model_dump(by_alias=True))

    assert restored == original


def test_config_written_before_the_budgets_existed_still_loads(tmp_path) -> None:
    """La regressione che conta: un'installazione aggiornata da mesi.

    Il suo ``config.json`` ha la sezione ``dream`` senza nessuno dei quattro
    campi nuovi; deve caricare senza errori e prendere i default inerti, non
    fallire l'avvio del gateway.
    """
    legacy = tmp_path / "config.json"
    legacy.write_text(
        json.dumps(
            {"agents": {"defaults": {"dream": {"enabled": True, "intervalH": 2}}}}
        ),
        encoding="utf-8",
    )

    dream = load_config(legacy).agents.defaults.dream

    assert dream.enabled is True
    assert dream.interval_h == 2
    assert dream.memory_budget_chars == 0
    assert dream.user_budget_chars == 0
    assert dream.soul_budget_chars == 0
    assert dream.review_every_runs == 12
