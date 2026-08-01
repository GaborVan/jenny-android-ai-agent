"""Tests for the Android on-device override in ``_apply_gateway_overrides``.

The Android entry point always calls ``run_gateway`` with a port, which flows
into ``_apply_gateway_overrides(..., ws_port=port)``. That function must keep
forcing ``websocket.enabled`` on by default (the on-device path never sets it
explicitly), while still respecting an explicit ``enabled: false`` a user
wrote into ``config.json`` themselves.
"""

from __future__ import annotations

from jenny.config.schema import Config
from jenny.gateway_runtime import _apply_gateway_overrides


def test_ws_port_forces_enabled_default_true_when_unset():
    config = Config()

    _apply_gateway_overrides(config, host="127.0.0.1", port=18790, ws_port=18790)

    assert config.websocket["enabled"] is True
    assert config.websocket["port"] == 18790


def test_ws_port_does_not_override_explicit_enabled_false():
    config = Config()
    config.websocket["enabled"] = False

    _apply_gateway_overrides(config, host="127.0.0.1", port=18790, ws_port=18790)

    assert config.websocket["enabled"] is False


def test_no_ws_port_leaves_enabled_untouched():
    config = Config()

    _apply_gateway_overrides(config, host="127.0.0.1", port=18790, ws_port=None)

    assert "enabled" not in config.websocket
