"""Il knob del giardiniere (``config.agents.defaults.gardener``).

Tre numeri, e ognuno risponde a una domanda diversa: ogni quanto si *guarda*
(``interval_min``), da quanto la conversazione deve tacere prima di entrare
(``idle_min``), e quanto si aspetta prima di ritornare sulla stessa materia
(``min_hours_between_passes``). Confonderli è facile e le conseguenze sono
opposte, quindi il test li tiene distinti uno per uno.
"""

from __future__ import annotations

import pytest

from jenny.config.schema import (
    GARDENER_DISTANCE_HOURS_MAX,
    GARDENER_IDLE_MIN_MAX,
    GARDENER_INTERVAL_MIN_MAX,
    AgentDefaults,
    Config,
    GardenerConfig,
)


def test_defaults() -> None:
    cfg = GardenerConfig()

    # Acceso come Dream e Atlas: senza righe di diario nuove il tick esce prima
    # di qualunque chiamata al provider, quindi su un'installazione che non usa i
    # progetti costa zero.
    assert cfg.enabled is True
    assert cfg.interval_min == 30
    assert cfg.idle_min == 30
    # Sei ore, e non è lo stesso numero di ``interval_min`` per caso: guardare
    # spesso costa nulla, *tornare* spesso sulla stessa materia è il degrado.
    assert cfg.min_hours_between_passes == 6


def test_the_schedule_comes_from_the_interval_not_from_the_other_two() -> None:
    # Valori tutti diversi e nessuno il default: così il test cade se il codice
    # costruisce lo schedule dal knob sbagliato.
    cfg = GardenerConfig(interval_min=7, idle_min=45, min_hours_between_passes=9)

    schedule = cfg.build_schedule()

    assert schedule.kind == "every"
    assert schedule.every_ms == 7 * 60_000


def test_the_description_names_all_three_numbers() -> None:
    """Va nei log all'avvio, ed è il solo posto dove si vede come è configurato:
    una descrizione che ne cita uno solo nasconde gli altri due."""
    text = GardenerConfig(
        interval_min=7, idle_min=45, min_hours_between_passes=9
    ).describe_schedule()

    assert "7min" in text and "45min" in text and "9h" in text


def test_dump_uses_camel_case_aliases() -> None:
    dumped = GardenerConfig(
        interval_min=15, idle_min=20, min_hours_between_passes=3
    ).model_dump(by_alias=True)

    assert dumped["intervalMin"] == 15
    assert dumped["idleMin"] == 20
    assert dumped["minHoursBetweenPasses"] == 3


def test_reads_camel_case_input() -> None:
    cfg = GardenerConfig(**{"intervalMin": 12, "idleMin": 8, "minHoursBetweenPasses": 4})

    assert (cfg.interval_min, cfg.idle_min, cfg.min_hours_between_passes) == (12, 8, 4)


def test_a_tick_of_zero_minutes_is_refused() -> None:
    """Zero significherebbe un tick continuo. I due orologi *possono* essere
    zero — spegnerli è una scelta legittima — ma il battito no."""
    with pytest.raises(Exception):
        GardenerConfig(interval_min=0)


@pytest.mark.parametrize("field", ["idle_min", "min_hours_between_passes"])
def test_the_two_clocks_can_be_switched_off(field: str) -> None:
    cfg = GardenerConfig(**{field: 0})

    assert getattr(cfg, field) == 0


def test_it_hangs_off_the_agent_defaults() -> None:
    assert isinstance(AgentDefaults().gardener, GardenerConfig)


# -- i tetti (T2.9) -----------------------------------------------------------
#
# I tre campi erano ``ge=`` senza tetto, e ``interval_min`` è quello che conta:
# diventa un ``every_ms``, quindi ``10**9`` pianifica diciannove secoli in avanti e
# arma una sveglia RTC per quella data. Innocuo finché il numero lo scrive solo un
# programmatore; non più da quando ``/gardener interval`` lo fa scrivere a mano.


@pytest.mark.parametrize(
    ("field", "over"),
    [
        ("interval_min", GARDENER_INTERVAL_MIN_MAX + 1),
        ("idle_min", GARDENER_IDLE_MIN_MAX + 1),
        ("min_hours_between_passes", GARDENER_DISTANCE_HOURS_MAX + 1),
    ],
)
def test_a_value_over_the_ceiling_is_refused(field: str, over: int) -> None:
    """Un valore appena sopra il tetto, non ``10**9``: un tetto si prova sul bordo.

    Con la clemenza montata su ``GardenerConfig`` invece che su ``AgentDefaults``
    questi tre rifiuti scomparirebbero tutti insieme, e il ``le=`` diventerebbe
    decorazione.
    """
    with pytest.raises(Exception):
        GardenerConfig(**{field: over})


@pytest.mark.parametrize(
    ("field", "top"),
    [
        ("interval_min", GARDENER_INTERVAL_MIN_MAX),
        ("idle_min", GARDENER_IDLE_MIN_MAX),
        ("min_hours_between_passes", GARDENER_DISTANCE_HOURS_MAX),
    ],
)
def test_the_top_of_each_range_is_accepted(field: str, top: int) -> None:
    """Il tetto è incluso: ``le``, non ``lt``. Un giorno esatto è una scelta."""
    assert getattr(GardenerConfig(**{field: top}), field) == top


def test_the_ceilings_are_the_numbers_the_command_names() -> None:
    """Il comando legge il range da ``model_fields``, non da una sua copia.

    Se un domani il ``le=`` si sposta e la costante resta indietro, i rifiuti di
    ``/gardener`` citerebbero un range che lo schema non applica più.
    """
    fields = GardenerConfig.model_fields

    assert fields["interval_min"].le == GARDENER_INTERVAL_MIN_MAX
    assert fields["idle_min"].le == GARDENER_IDLE_MIN_MAX
    assert fields["min_hours_between_passes"].le == GARDENER_DISTANCE_HOURS_MAX


def test_a_legacy_value_over_the_ceiling_is_clamped_and_not_quarantined() -> None:
    """Il tetto non deve poter cancellare la config di chi aggiorna.

    ``loader._load_with_recovery`` reagisce a un ``ValidationError`` provando il
    ``.bak`` — che ha lo stesso numero fuori range — e poi mettendo il file in
    quarantena e ripartendo dai **default**: provider e chiave compresi. Per un
    ``intervalMin`` scritto da una versione senza tetti sarebbe uno scambio
    pessimo, e renderebbe irraggiungibile proprio la via d'uscita
    (``/gardener off`` passa da ``store.mutate``, che rilegge il file).
    """
    config = Config.model_validate({
        "providers": {
            "default": "ds",
            "providers": [{"name": "ds", "format": "openai_compat", "api_key": "sk-x"}],
        },
        "agents": {"defaults": {"gardener": {
            "intervalMin": 10 ** 9,
            "idleMin": -5,
            "minHoursBetweenPasses": 10 ** 9,
        }}},
    })

    gardener = config.agents.defaults.gardener
    # Limitato al tetto, non riportato al default: il numero fuori range dice in
    # che direzione andava la scelta, e 30 minuti farebbero lavorare il
    # giardiniere molto più di quanto chiunque avesse chiesto.
    assert gardener.interval_min == GARDENER_INTERVAL_MIN_MAX
    assert gardener.idle_min == 0
    assert gardener.min_hours_between_passes == GARDENER_DISTANCE_HOURS_MAX
    # E il resto del file è ancora lì: è la cosa che la quarantena avrebbe perso.
    assert config.providers.providers[0].api_key == "sk-x"


def test_the_clamp_reads_the_snake_case_alias_too() -> None:
    """Un `config.json` scritto a mano usa spesso lo snake_case: entrambi gli alias
    portano al campo, quindi entrambi devono passare dalla clemenza — altrimenti il
    tetto boccia solo una delle due grafie."""
    config = Config.model_validate(
        {"agents": {"defaults": {"gardener": {"interval_min": 10 ** 9}}}}
    )

    assert config.agents.defaults.gardener.interval_min == GARDENER_INTERVAL_MIN_MAX


def test_a_value_inside_the_range_is_left_exactly_alone() -> None:
    """La clemenza non deve diventare una normalizzazione: un numero valido
    attraversa il parse identico, e il file non viene "corretto" da nessuno."""
    config = Config.model_validate(
        {"agents": {"defaults": {"gardener": {"intervalMin": 45, "idleMin": 0}}}}
    )

    assert config.agents.defaults.gardener.interval_min == 45
    assert config.agents.defaults.gardener.idle_min == 0


def test_a_non_numeric_value_is_not_guessed_at() -> None:
    """``clamp_raw`` limita numeri; non prova a interpretare una stringa. Un
    ``intervalMin: "presto"`` è un refuso, e la validazione del campo è il posto
    dove viene detto — non qui, indovinando."""
    with pytest.raises(Exception):
        GardenerConfig.model_validate({"intervalMin": "presto"})
