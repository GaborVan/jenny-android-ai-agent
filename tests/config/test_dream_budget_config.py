"""Test per i budget della memoria lunga su ``DreamConfig``.

Due cose distinte da tenere ferme. La prima sono i tetti di spedizione, che
sono un numero misurato sul device e non una stima: cambiarli è una decisione,
non un refactor. La seconda è il *segno* del vincolo — zero resta legale, è
"misura, non applicare", e questi test esistono soprattutto per impedire che
qualcuno stringa ``ge=0`` in ``gt=0`` durante una pulizia.
"""

from __future__ import annotations

import json

import pytest

from jenny.config.loader import load_config
from jenny.config.schema import DreamConfig
from jenny.pydantic_compat import ValidationError


def test_memory_and_user_ship_enforced_at_two_thousand() -> None:
    """I due tetti veri, scelti dalle misure del device.

    ``MEMORY.md`` stava a 3.019 caratteri e ``USER.md`` a 1.626 dopo due
    passaggi di review: 2.000 morde subito il primo e tiene il secondo. Sono
    numeri misurati, non arrotondamenti — se cambiano, che sia perché è cambiata
    una misura.
    """
    cfg = DreamConfig()

    assert cfg.memory_budget_chars == 2000
    assert cfg.user_budget_chars == 2000
    assert cfg.review_every_runs == 12


def test_soul_stays_measured_but_not_enforced() -> None:
    # ``SOUL.md`` non segue gli altri due: mescola identità e vincoli di
    # piattaforma, e un rifiuto di scrittura non sa su quale delle due sta
    # premendo. Lo strumento giusto lì è il review pass, che legge e sceglie.
    assert DreamConfig().soul_budget_chars == 0


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
    # Non avendo le chiavi, prende i default nuovi: è l'unica installazione
    # esistente che il cambio di default raggiunge davvero.
    assert dream.memory_budget_chars == 2000
    assert dream.user_budget_chars == 2000
    assert dream.soul_budget_chars == 0
    assert dream.review_every_runs == 12


def test_a_zero_already_on_disk_wins_over_the_new_default(tmp_path) -> None:
    """Il caso del device, e il motivo per cui alzare il default non basta.

    ``config/loader.py`` serializza con ``by_alias=True`` e senza
    ``exclude_defaults``: ogni ``config.json`` scritto da quando questi campi
    esistono porta dentro il valore di allora. Sul Titan 2 quel valore è ``0``,
    e continuerà a vincere su qualunque default in Python finché non lo si
    riscrive — con ``/dream budget memory 2000``, che è il motivo per cui quel
    comando esiste. Un'installazione nuova e il device divergono, e questo test
    è il posto in cui la divergenza è dichiarata invece che scoperta.
    """
    existing = tmp_path / "config.json"
    existing.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "dream": {"memoryBudgetChars": 0, "userBudgetChars": 0}
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    dream = load_config(existing).agents.defaults.dream

    assert dream.memory_budget_chars == 0
    assert dream.user_budget_chars == 0
