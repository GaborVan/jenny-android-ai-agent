"""Lo slash command ``/gardener``.

Esiste per due ragioni, e la seconda è la vera: **senza di lui il passo T4 non è
collaudabile.** I tre orologi dell'innesco — delta, trenta minuti di fermo, sei
ore di distanza dall'ultima passata — rendono la strada naturale impossibile da
percorrere in una sessione di prova.

Qui si verifica il routing, il modo in cui il comando capisce *su quale progetto*
lavorare, e che ogni esito dica davvero cosa è successo: i modi di non fare
niente sono tre, e vogliono dire cose molto diverse.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jenny.agent.gardener import GardenerOutcome
from jenny.bus.events import InboundMessage
from jenny.command.builtin import (
    _format_gardener_outcome,
    cmd_gardener,
    register_builtin_commands,
)
from jenny.command.router import CommandContext, CommandRouter


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


def _ctx(key: str, args: str = "") -> CommandContext:
    msg = InboundMessage(
        channel="websocket", sender_id="user", chat_id="c1",
        content=f"/gardener {args}".strip(),
    )
    return CommandContext(
        msg=msg, session=None, key=key, raw=msg.content, args=args,
        loop=SimpleNamespace(bus=None),
    )


class TestRegistration:
    def test_is_dispatchable_with_and_without_a_project(self, router):
        assert router.is_dispatchable_command("/gardener")
        assert router.is_dispatchable_command("/gardener viaggio")

    def test_is_listed_among_builtin_commands(self):
        from jenny.command.builtin import BUILTIN_COMMAND_SPECS

        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}

        assert "/gardener" in specs
        assert specs["/gardener"].arg_hint == "[project]"


@pytest.fixture()
def no_background(monkeypatch):
    """Il comando avvia la passata in una task e risponde subito.

    Nei test che guardano *solo* la scelta del bersaglio quella task non ha un
    bus dove pubblicare: lasciarla partire vuol dire un'eccezione raccolta da
    nessuno e un event loop che si chiude sotto una coroutine viva — il tipo di
    rumore che diventa flakiness in un altro file. Qui si chiude, e resta la sola
    domanda che il test pone.
    """
    started: list[str] = []

    def _swallow(coro):
        started.append(getattr(coro, "__name__", "coro"))
        coro.close()
        return None

    monkeypatch.setattr(asyncio, "create_task", _swallow)
    return started


class TestPickingTheProject:
    async def test_outside_a_project_it_refuses_and_says_how(self):
        """Un rifiuto che dice **dove**, non solo che qui non si può: la lezione
        dei rifiuti del passo 6, e la stessa forma del rifiuto di
        ``journal_append`` fuori da un progetto. Un rifiuto che manda via a mani
        vuote costa un altro turno."""
        out = await cmd_gardener(_ctx("unified:default"))

        assert "not a project" in out.content
        assert "/gardener <project>" in out.content

    async def test_inside_a_project_the_target_is_that_project(self, no_background):
        """Nessun argomento da scrivere: la conversazione in cui si è **è** il
        bersaglio. Il nome viene dalla chiave, come per lo scope di un turno."""
        out = await cmd_gardener(_ctx("project:viaggio-pazzo"))

        assert "viaggio-pazzo" in out.content
        assert no_background, "la passata deve partire, non solo essere annunciata"

    async def test_a_named_project_wins_over_the_current_one(self, no_background):
        out = await cmd_gardener(_ctx("project:viaggio", args="altro"))

        assert "altro" in out.content


class TestOutcomeMessages:
    def test_every_status_says_something_specific(self):
        messages = {
            status: _format_gardener_outcome(
                "viaggio",
                GardenerOutcome(status=status, elapsed=1.25, lines=4, writes=2, detail="boom"),
            )
            for status in (
                "skipped_no_delta",
                "written",
                "nothing_to_promote",
                "no_write",
                "incomplete",
                "failed",
            )
        }

        assert len(set(messages.values())) == len(messages)
        assert all(text.strip() for text in messages.values())

    def test_nothing_new_says_it_cost_nothing(self):
        text = _format_gardener_outcome("viaggio", GardenerOutcome(status="skipped_no_delta"))

        assert "no tokens spent" in text

    def test_the_two_ways_of_writing_nothing_are_not_the_same_sentence(self):
        """«non c'era niente che meritasse una pagina» e «ho provato e sono stato
        bloccato» sono lo stesso silenzio visto da fuori e due fatti opposti: il
        primo chiude il materiale, il secondo lo lascia da rileggere. Se le due
        frasi si somigliassero, la differenza si perderebbe proprio dove serve."""
        promoted = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="nothing_to_promote", lines=4, elapsed=2.0)
        )
        blocked = _format_gardener_outcome(
            "viaggio", GardenerOutcome(status="no_write", lines=4, elapsed=2.0)
        )

        assert "marked as read" in promoted
        assert "will try again" in blocked
