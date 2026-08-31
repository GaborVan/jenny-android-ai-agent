"""Lo slash command ``/atlas``.

Delega tutto a ``run_atlas``: qui si verifica il routing, il passaggio di
``force``, e che ogni esito abbia un messaggio che dice davvero cosa è
successo — un comando che risponde "fatto" senza aver fatto nulla è peggio di
uno che spiega perché non l'ha fatto.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jenny.agent.atlas import AtlasOutcome
from jenny.bus.events import InboundMessage
from jenny.command.builtin import _format_atlas_outcome, register_builtin_commands
from jenny.command.router import CommandContext, CommandRouter


@pytest.fixture()
def router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


class TestRegistration:
    def test_is_dispatchable_with_and_without_args(self, router):
        assert router.is_dispatchable_command("/atlas")
        assert router.is_dispatchable_command("/atlas force")

    def test_is_listed_among_builtin_commands(self):
        from jenny.command.specs import BUILTIN_COMMAND_SPECS

        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}

        assert "/atlas" in specs
        assert specs["/atlas"].arg_hint == "[force]"


class TestOutcomeMessages:
    def test_every_status_says_something_specific(self):
        messages = {
            status: _format_atlas_outcome(AtlasOutcome(status=status, elapsed=1.25, detail="boom"))
            for status in (
                "skipped_no_wikis",
                "skipped_unchanged",
                "written",
                "no_write",
                "incomplete",
                "failed",
            )
        }

        assert len(set(messages.values())) == len(messages)
        assert all(text.strip() for text in messages.values())

    def test_no_wikis_points_at_the_fix(self):
        text = _format_atlas_outcome(AtlasOutcome(status="skipped_no_wikis"))

        assert "workspace/wikis/" in text

    def test_unchanged_mentions_the_force_escape_hatch(self):
        text = _format_atlas_outcome(AtlasOutcome(status="skipped_unchanged"))

        assert "/atlas force" in text
        assert "no tokens spent" in text

    def test_blocked_write_says_the_next_run_will_retry(self):
        text = _format_atlas_outcome(AtlasOutcome(status="no_write", elapsed=2.0))

        assert "retry" in text

    def test_failure_carries_the_reason(self):
        text = _format_atlas_outcome(AtlasOutcome(status="failed", elapsed=0.5, detail="boom"))

        assert "boom" in text


class TestDispatch:
    @pytest.mark.asyncio
    async def test_force_argument_is_forwarded(self, router, monkeypatch, tmp_path):
        seen: dict[str, object] = {}

        async def _fake_run_atlas(agent, *, store=None, force=False):
            seen["force"] = force
            return AtlasOutcome(status="written", elapsed=0.1)

        monkeypatch.setattr("jenny.agent.atlas.run_atlas", _fake_run_atlas)

        published: list = []
        loop = SimpleNamespace(
            bus=SimpleNamespace(publish_outbound=_collect(published)),
            context=SimpleNamespace(timezone=None),
        )
        msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="/atlas force")
        ctx = CommandContext(msg=msg, session=None, key="k", raw="/atlas force", args="force", loop=loop)

        ack = await router.dispatch(ctx)
        await _drain()

        assert "Mapping the wiki" in ack.content
        assert seen["force"] is True
        assert published and "Atlas updated" in published[0].content

    @pytest.mark.asyncio
    async def test_plain_invocation_does_not_force(self, router, monkeypatch):
        seen: dict[str, object] = {}

        async def _fake_run_atlas(agent, *, store=None, force=False):
            seen["force"] = force
            return AtlasOutcome(status="skipped_unchanged")

        monkeypatch.setattr("jenny.agent.atlas.run_atlas", _fake_run_atlas)

        published: list = []
        loop = SimpleNamespace(
            bus=SimpleNamespace(publish_outbound=_collect(published)),
            context=SimpleNamespace(timezone=None),
        )
        msg = InboundMessage(channel="websocket", sender_id="u", chat_id="default", content="/atlas")
        ctx = CommandContext(msg=msg, session=None, key="k", raw="/atlas", args="", loop=loop)

        await router.dispatch(ctx)
        await _drain()

        assert seen["force"] is False
        assert published and "hasn't changed" in published[0].content


def _collect(sink: list):
    async def _publish(message):
        sink.append(message)

    return _publish


async def _drain(timeout: float = 30.0) -> None:
    """Aspetta il task fire-and-forget creato dal comando. Davvero.

    Era ``for _ in range(10): await asyncio.sleep(0)``, cioè la stessa forma che
    ha fatto rosso la CI su Dream il 28/08/2026 (v. il gemello documentato in
    ``test_dream_command.py``): ``cmd_atlas`` fa ``asyncio.create_task`` senza
    tenere il manico, e un ``sleep(0)`` cede il controllo ma **non** lascia
    passare tempo — se il lavoro attraversa ``asyncio.to_thread``, dieci giri di
    event loop non bastano su una macchina carica.

    Si aspettano quindi gli oggetti giusti: i task vivi che non sono questo test.
    Si rileggono a ogni giro perché un task può aprirne un altro. Il tetto serve
    solo a non appendere la suite, e se scatta lo dice invece di lasciar fallire
    un assert tre righe più in là.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    current = asyncio.current_task()
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            return
        remaining = deadline - loop.time()
        assert remaining > 0, f"task ancora in volo dopo {timeout}s: {pending}"
        await asyncio.wait(pending, timeout=remaining)
