"""Gateway runtime for jenny."""

from loguru import logger

from jenny.bus.events import INTERNAL_CHANNEL
from jenny.config.schema import Config

__all__ = ["INTERNAL_CHANNEL"]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_runtime_config() -> Config:
    """Load config from workspace."""
    from jenny.config.loader import load_config, resolve_config_env_vars

    try:
        loaded = resolve_config_env_vars(load_config())
    except ValueError as e:
        logger.error("Config error: {}", e)
        raise RuntimeError(f"Failed to load runtime config: {e}") from e
    return loaded


def _apply_gateway_overrides(
    config: Config,
    *,
    host: str | None = None,
    port: int | None = None,
    ws_port: int | None = None,
) -> Config:
    """Return *config* with gateway and websocket overrides applied in place."""
    if host is not None:
        config.gateway.host = host
    if port is not None:
        config.gateway.port = port
    if ws_port is not None:
        config.websocket["port"] = ws_port
        if host is not None:
            config.websocket["host"] = host
        config.websocket.setdefault("enabled", True)
    return config


# ---------------------------------------------------------------------------
# Gateway runtime
# ---------------------------------------------------------------------------


async def _run_gateway(
    config: Config | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    ws_port: int | None = None,
) -> None:
    """Shared gateway runtime.

    Invoked programmatically by ``run_gateway`` (e.g. from Android/Chaquopy).
    When called from an embedded context, exceptions are allowed to propagate.

    Il wiring vero e proprio (costruzione del grafo, onboarding differito, drain
    di shutdown) vive in ``runtime.container.GatewayContainer``: questo entry
    point resta la funzione module-level patchabile dai test e dall'``android_entry``.
    """
    from jenny.runtime.container import GatewayContainer

    if config is None:
        config = _load_runtime_config()

    _apply_gateway_overrides(config, host=host, port=port, ws_port=ws_port)

    container = GatewayContainer(config)
    container.build()
    await container.run()
