"""Test della meccanica di dispatch del ``CommandRouter``.

Completa ``test_router_dispatchable.py`` (che copre il predicato): qui si
verificano i tre tier di dispatch, l'ordine longest-prefix-first,
l'estrazione degli ``args`` e la case-insensitivity.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jenny.command.router import CommandContext, CommandRouter


def _ctx(raw: str) -> CommandContext:
    return CommandContext(msg=MagicMock(), session=None, key="unified:default", raw=raw)


def _handler(calls: list[tuple[str, str]], name: str):
    async def handle(ctx: CommandContext):
        calls.append((name, ctx.args))
        return f"handled:{name}"

    return handle


async def test_exact_dispatch_and_case_insensitivity() -> None:
    calls: list[tuple[str, str]] = []
    router = CommandRouter()
    router.exact("/nuovo", _handler(calls, "nuovo"))

    assert await router.dispatch(_ctx("/nuovo")) == "handled:nuovo"
    assert await router.dispatch(_ctx("/NUOVO")) == "handled:nuovo"
    assert calls == [("nuovo", ""), ("nuovo", "")]


async def test_prefix_dispatch_extracts_args() -> None:
    calls: list[tuple[str, str]] = []
    router = CommandRouter()
    router.prefix("/goal ", _handler(calls, "goal"))

    assert await router.dispatch(_ctx("/goal migra il database")) == "handled:goal"
    assert calls == [("goal", "migra il database")]


async def test_longest_prefix_wins_regardless_of_registration_order() -> None:
    calls: list[tuple[str, str]] = []
    router = CommandRouter()
    router.prefix("/team ", _handler(calls, "corto"))
    router.prefix("/team run ", _handler(calls, "lungo"))

    assert await router.dispatch(_ctx("/team run job")) == "handled:lungo"
    assert calls == [("lungo", "job")]


async def test_exact_tier_beats_prefix_tier() -> None:
    calls: list[tuple[str, str]] = []
    router = CommandRouter()
    router.prefix("/model", _handler(calls, "prefisso"))
    router.exact("/model", _handler(calls, "esatto"))

    assert await router.dispatch(_ctx("/model")) == "handled:esatto"
    assert calls == [("esatto", "")]


async def test_unhandled_command_returns_none() -> None:
    router = CommandRouter()
    assert await router.dispatch(_ctx("/sconosciuto")) is None
    assert await router.dispatch_priority(_ctx("/sconosciuto")) is None


async def test_priority_tier_is_separate() -> None:
    """I comandi priority non passano da dispatch(): solo da dispatch_priority()."""
    calls: list[tuple[str, str]] = []
    router = CommandRouter()
    router.priority("/stop", _handler(calls, "stop"))

    assert await router.dispatch(_ctx("/stop")) is None
    assert await router.dispatch_priority(_ctx("/stop")) == "handled:stop"
    assert router.is_priority("  /STOP  ") is True
    assert router.is_dispatchable_command("/stop") is False
