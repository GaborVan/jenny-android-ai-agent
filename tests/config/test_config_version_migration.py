"""Migrazioni del config guidate da ``configVersion``.

Il punto di tutto il meccanismo: ``loader.py`` serializza il config *includendo
i default*, quindi un file scritto quando un default era X porta X per sempre e
alzare il default nello schema non raggiunge chi aggiorna. Il contatore di
versione e l'unica cosa che distingue "valore scelto" da "vecchio default
rimasto scritto".
"""

from __future__ import annotations

import json

from jenny.config.loader import load_config
from jenny.config.schema import CURRENT_CONFIG_VERSION, AgentDefaults, Config

_NEW_CONCURRENCY = AgentDefaults.model_fields["max_concurrent_subagents"].default


def test_legacy_config_without_version_is_migrated() -> None:
    """Il caso reale: installazione che aggiorna, con il vecchio 1 nel file."""
    config = Config.model_validate({"agents": {"defaults": {"maxConcurrentSubagents": 1}}})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_snake_case_key_is_migrated_too() -> None:
    """I config legacy usano entrambe le convenzioni."""
    config = Config.model_validate({"agents": {"defaults": {"max_concurrent_subagents": 1}}})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY


def test_a_deliberate_one_survives_once_the_version_is_stamped() -> None:
    """Dopo la migrazione i valori nel file *sono* scelte, e restano.

    È la ragione d'essere del contatore: senza, ogni boot rispingerebbe a 3 un
    utente che ha deciso di volerne uno solo.
    """
    config = Config.model_validate({
        "configVersion": CURRENT_CONFIG_VERSION,
        "agents": {"defaults": {"maxConcurrentSubagents": 1}},
    })
    assert config.agents.defaults.max_concurrent_subagents == 1


def test_a_non_default_legacy_value_is_left_alone() -> None:
    """La migrazione e condizionata sul valore vecchio esatto, non sulla versione."""
    config = Config.model_validate({"agents": {"defaults": {"maxConcurrentSubagents": 2}}})
    assert config.agents.defaults.max_concurrent_subagents == 2


def test_a_fresh_config_is_already_current() -> None:
    config = Config.model_validate({})
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_a_garbage_version_degrades_to_zero_instead_of_raising() -> None:
    """Un config corrotto a mano non deve impedire il boot del gateway."""
    config = Config.model_validate({
        "configVersion": "not a number",
        "agents": {"defaults": {"maxConcurrentSubagents": 1}},
    })
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


def test_the_stamp_is_serialized_so_the_next_write_persists_it() -> None:
    """Nessuna scrittura dedicata: lo stamp viaggia col primo dump ordinario."""
    dumped = Config.model_validate({}).model_dump(mode="json", by_alias=True)
    assert dumped["configVersion"] == CURRENT_CONFIG_VERSION


def test_migration_runs_through_the_real_loader(tmp_path) -> None:
    """Il percorso che conta: file su disco -> ``load_config``."""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agents": {"defaults": {"maxConcurrentSubagents": 1}}}),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.agents.defaults.max_concurrent_subagents == _NEW_CONCURRENCY
    assert config.config_version == CURRENT_CONFIG_VERSION


async def test_persist_stamps_the_file_once(tmp_path) -> None:
    """Lo stamp va su disco una volta: senza, la migrazione rigira a ogni boot."""
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"agents": {"defaults": {"maxConcurrentSubagents": 1}}}),
        encoding="utf-8",
    )

    assert await persist_schema_migrations(config_path=path) is True
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["configVersion"] == CURRENT_CONFIG_VERSION
    assert written["agents"]["defaults"]["maxConcurrentSubagents"] == _NEW_CONCURRENCY

    # Seconda passata: niente da fare, e il file non viene toccato.
    before = path.read_bytes()
    assert await persist_schema_migrations(config_path=path) is False
    assert path.read_bytes() == before


async def test_persist_preserves_unknown_keys(tmp_path) -> None:
    """Passa da ``store.mutate``, quindi le chiavi che questa versione non
    conosce sopravvivono alla riscrittura."""
    from jenny.config.store import persist_schema_migrations

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({
            "agents": {"defaults": {"maxConcurrentSubagents": 1}},
            "somethingFromTheFuture": {"keep": "me"},
        }),
        encoding="utf-8",
    )
    await persist_schema_migrations(config_path=path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["somethingFromTheFuture"] == {"keep": "me"}
