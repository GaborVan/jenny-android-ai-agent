"""Profili provider: più endpoint, e il "locale" che non pretende una chiave.

Il caso che questi test proteggono è quello che prima **non si poteva
configurare**: un endpoint sulla macchina dell'utente — un modello in LAN, o un
bridge che espone un abbonamento CLI dietro un'API OpenAI-compatibile — non ha
una ``api_key``, e il factory la pretendeva comunque. L'unica via era inventarne
una finta.

Il resto è la parte che rende utili i profili: un ``apiBase`` arbitrario arriva
intatto al provider, e un preset può nominare *quale* profilo usare.
"""

from __future__ import annotations

import pytest

from jenny.config.schema import Config, ProviderConfig
from jenny.providers.factory import make_provider
from jenny.webui.settings_api import _provider_list_simple


def _config(*, tier: str = "api", api_key: str | None = "sk-real", base: str | None = None) -> Config:
    provider: dict[str, object] = {
        "name": "profile",
        "format": "openai_compat",
        "apiKey": api_key,
        "tier": tier,
        "apiBase": base or "https://api.example.com/v1",
    }
    return Config.model_validate(
        {
            "providers": {"providers": [provider]},
            "agents": {"defaults": {"model": "test-model"}},
        }
    )


# ── Il campo tier ──────────────────────────────────────────────────────────


def test_the_default_tier_is_a_paid_api() -> None:
    assert ProviderConfig(name="x", format="openai_compat").tier == "api"


@pytest.mark.parametrize("tier", ["api", "subscription", "local"])
def test_known_tiers_parse(tier: str) -> None:
    assert ProviderConfig(name="x", format="openai_compat", tier=tier).tier == tier


def test_an_unknown_tier_is_refused() -> None:
    from jenny.pydantic_compat import ValidationError

    with pytest.raises(ValidationError):
        ProviderConfig(name="x", format="openai_compat", tier="free-lunch")


# ── Il factory: chi ha bisogno di una chiave ───────────────────────────────


def test_a_paid_provider_still_requires_a_key() -> None:
    with pytest.raises(RuntimeError, match="api_key is required"):
        make_provider(_config(tier="api", api_key=None))


@pytest.mark.parametrize("tier", ["local", "subscription"])
def test_a_local_or_subscription_profile_needs_no_key(tier: str) -> None:
    provider = make_provider(
        _config(tier=tier, api_key=None, base="http://192.168.1.9:8080/v1")
    )

    assert provider.api_base == "http://192.168.1.9:8080/v1"
    assert provider._base_url() == "http://192.168.1.9:8080/v1"


def test_a_local_profile_with_a_key_still_uses_it() -> None:
    provider = make_provider(_config(tier="local", api_key="sk-local"))

    assert provider._api_key_for_client == "sk-local"


def test_a_keyless_local_profile_sends_the_placeholder() -> None:
    # Il segnaposto è quello che il provider mandava già prima per una chiave
    # vuota: i server di casa lo ignorano, e non c'è un ramo nuovo da testare.
    provider = make_provider(_config(tier="local", api_key=None))

    assert provider._api_key_for_client == "no-key"


# ── apiBase arbitrario ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "base",
    [
        "http://127.0.0.1:8080/v1",
        "http://192.168.1.9:8080/v1",
        "https://bridge.tailnet-name.ts.net/v1",
        "https://host.example.com/openai/v1/",
    ],
)
def test_the_base_url_is_kept_verbatim(base: str) -> None:
    provider = make_provider(_config(tier="local", api_key=None, base=base))

    assert provider.api_base == base
    # ``_base_url`` toglie solo lo slash finale: nessuna riscrittura di host,
    # porta o percorso.
    assert provider._base_url() == base.rstrip("/")


# ── Profili e preset ───────────────────────────────────────────────────────


def test_presets_can_name_a_provider_profile() -> None:
    cfg = Config.model_validate(
        {
            "providers": {
                "providers": [
                    {
                        "name": "subscription-bridge",
                        "format": "openai_compat",
                        "apiBase": "https://bridge.tailnet-name.ts.net/v1",
                        "tier": "subscription",
                    },
                    {
                        "name": "paid",
                        "format": "openai_compat",
                        "apiKey": "sk-real",
                        "tier": "api",
                    },
                ],
                "default": "paid",
            },
            "modelPresets": {
                "cheap": {"provider": "subscription-bridge", "model": "gpt-5-codex"},
                "frontier": {"provider": "paid", "model": "gpt-5"},
            },
        }
    )

    assert cfg.providers.default == "paid"
    assert cfg.get_active_provider().name == "paid"
    assert cfg.model_presets["cheap"].provider == "subscription-bridge"
    assert cfg.model_presets["cheap"].model == "gpt-5-codex"


def test_the_provider_list_carries_the_tier_for_the_ui() -> None:
    cfg = _config(tier="subscription", api_key=None, base="https://bridge.example/v1")

    payload = _provider_list_simple(cfg)

    assert payload[0]["tier"] == "subscription"
    # Un profilo senza chiave ma con un apiBase è configurato, non incompleto:
    # è la lettura su cui la UI decide se mostrare un avviso.
    assert payload[0]["configured"] is True
