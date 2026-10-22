"""Preset di provider: il "bridge di casa", raggiungibile da Tailscale.

Sul PC di casa gira un bridge che espone i CLI (Codex/Claude/Gemini) come API
OpenAI-compatibile su un host ``*.ts.net`` privato, dietro un bearer token.
Configurarlo a mano significava copiare a mano un URL lungo, scegliere il tier
giusto (``subscription``: la chiave non è un errore se manca) e sapere che il
token è facoltativo a livello di schema. Qui c'è il preset che riempie i campi.

**Il preset è un punto di partenza, non un valore cablato**: ``build_provider_config``
accetta nome, URL, token e tier come override, e quello che salva è comunque un
``ProviderConfig`` normale — modificabile dalla UI come qualunque altro profilo.
Cambiare indirizzo o token è una modifica di config, non di codice.

Il token vive nella config dell'utente e non compare mai qui: questo modulo
conosce solo l'URL pubblico del bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jenny.config.schema import ProviderConfig

__all__ = [
    "DEFAULT_HOME_BRIDGE_BASE_URL",
    "HOME_BRIDGE_DEFAULT_MODELS",
    "HOME_BRIDGE_PRESET_ID",
    "ProviderPreset",
    "build_provider_config",
    "get_preset",
    "presets_payload",
    "provider_presets",
]

HOME_BRIDGE_PRESET_ID = "home-bridge-tailscale"

#: L'URL del bridge come lo vede un dispositivo nel tailnet. Il certificato è
#: valido (Tailscale lo emette), quindi HTTPS non è una forzatura: è il motivo
#: per cui Android accetta l'endpoint, che non è loopback.
DEFAULT_HOME_BRIDGE_BASE_URL = (
    "https://gaborvan-nitro-an515-42-1.taild5ff76.ts.net:8899/v1"
)

#: I modelli che il bridge espone di norma. Sono un suggerimento per la UI,
#: non una lista chiusa: quella vera la dice ``GET /v1/models`` del bridge.
HOME_BRIDGE_DEFAULT_MODELS = ("codex", "claude", "gemini")


@dataclass(frozen=True)
class ProviderPreset:
    """Un profilo provider precompilato, con i valori che la UI deve scrivere."""

    id: str
    name: str
    format: str
    tier: str
    api_base: str
    models: tuple[str, ...]
    key_required: bool


_HOME_BRIDGE = ProviderPreset(
    id=HOME_BRIDGE_PRESET_ID,
    name="Домашній міст (Tailscale)",
    format="openai_compat",
    # ``subscription``: il bridge parla per un abbonamento CLI, non per una
    # chiave a consumo — ed è questo che rende la chiave facoltativa nel factory.
    tier="subscription",
    api_base=DEFAULT_HOME_BRIDGE_BASE_URL,
    models=HOME_BRIDGE_DEFAULT_MODELS,
    key_required=False,
)


def provider_presets() -> tuple[ProviderPreset, ...]:
    """Tutti i preset noti, nell'ordine in cui la UI li offre."""
    return (_HOME_BRIDGE,)


def get_preset(preset_id: str) -> ProviderPreset | None:
    """Il preset con quell'id, o ``None`` — un id sconosciuto non è un errore."""
    for preset in provider_presets():
        if preset.id == preset_id:
            return preset
    return None


def presets_payload() -> list[dict[str, Any]]:
    """I preset in forma JSON, per il payload delle impostazioni."""
    return [
        {
            "id": preset.id,
            "name": preset.name,
            "format": preset.format,
            "tier": preset.tier,
            "api_base": preset.api_base,
            "models": list(preset.models),
            "key_required": preset.key_required,
        }
        for preset in provider_presets()
    ]


def build_provider_config(
    preset: ProviderPreset,
    *,
    name: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    tier: str | None = None,
) -> ProviderConfig:
    """Un ``ProviderConfig`` da *preset*, con gli override dell'utente.

    Gli override vuoti o assenti valgono "tieni il valore del preset": un campo
    lasciato in bianco nella UI non deve cancellare l'URL di default. Un tier
    fuori dai tre noti viene ignorato per lo stesso motivo — meglio il default
    del preset di un valore che il factory poi rifiuterebbe.
    """
    resolved_name = (name or "").strip() or preset.name
    resolved_base = (api_base or "").strip() or preset.api_base
    resolved_tier = tier if tier in ("api", "subscription", "local") else preset.tier
    resolved_key = (api_key or "").strip() or None
    return ProviderConfig(
        name=resolved_name,
        format=preset.format,  # type: ignore[arg-type]
        api_key=resolved_key,
        api_base=resolved_base,
        tier=resolved_tier,  # type: ignore[arg-type]
    )
