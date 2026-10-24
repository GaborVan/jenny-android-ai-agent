"""Contratto del tool e ciclo di vita della sessione OBD."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jenny.agent.tools.loader import ToolLoader
from jenny.agent.tools.obd import ObdTool
from jenny.config.schema import ToolsConfig
from jenny.config.tool_schemas import ObdConfig
from jenny.obd.transport import MockTransport


@pytest.fixture
async def connected():
    transport = MockTransport(["ELM327 v1.5>"] + ["OK>"] * 4)
    tool = ObdTool(transport_factory=lambda _: transport)
    assert json.loads(await tool.execute("connect", device="OBD"))["connected"]
    yield tool, transport
    await tool.close()


async def test_read_codes(connected):
    tool, transport = connected
    transport.responses.extend(["4303010420C100>", "4703010000>", "NO DATA>"])
    result = json.loads(await tool.execute("read_codes"))
    assert result == {"stored": ["P0301", "P0420", "U0100"], "pending": ["P0301"], "permanent": []}
    assert transport.writes[-3:] == ["03", "07", "0A"]


@pytest.mark.parametrize("confirm", [False, None, "true", 1])
async def test_clear_refused(connected, confirm):
    tool, transport = connected
    before = transport.writes.copy()
    result = json.loads(await tool.execute("clear_codes", confirm=confirm))
    assert "confirm=true" in result["error"]
    assert "readiness" in result["error"]
    assert transport.writes == before


async def test_clear_acknowledged(connected):
    tool, transport = connected
    transport.responses.append("44>")
    assert json.loads(await tool.execute("clear_codes", confirm=True))["cleared"]
    assert transport.writes[-1] == "04"


async def test_clear_no_ack(connected):
    tool, transport = connected
    transport.responses.append("NO DATA>")
    assert "error" in json.loads(await tool.execute("clear_codes", confirm=True))


async def test_live(connected):
    tool, transport = connected
    transport.responses.extend(["410C1AF8>", "410D64>", "41FE12AB>", "NO DATA>"])
    result = json.loads(await tool.execute("live", pids=["0c", "0D", "FE", "05"]))
    assert result["0C"][0]["value"] == 1726
    assert result["0D"][0]["value"] == 100
    assert result["FE"][0]["raw"] == [18, 171]
    assert result["05"] == []


async def test_invalid_pids_do_not_write(connected):
    tool, transport = connected
    before = transport.writes.copy()
    assert "error" in json.loads(await tool.execute("live", pids=["04\r04"]))
    assert transport.writes == before


@pytest.mark.parametrize("action", ["read_codes", "clear_codes", "live", "vin", "readiness"])
async def test_disconnected(action):
    tool = ObdTool()
    assert "No OBD adapter connected" in json.loads(await tool.execute(action))["error"]


async def test_status_no_hardware():
    def fail(_):
        pytest.fail("status must not touch hardware")

    tool = ObdTool(transport_factory=fail)
    assert json.loads(await tool.execute("status"))["connected"] is False


def test_enable_toggle():
    config = ToolsConfig()
    ctx = SimpleNamespace(config=config, android_context=object())
    # On by default: without an adapter the tool answers with a clear error, so
    # the user should not have to hunt for a switch before the first connection.
    assert ObdTool.enabled(ctx)
    config.obd.enable = False
    assert not ObdTool.enabled(ctx)
    assert isinstance(ObdTool.create(ctx), ObdTool)
    assert ObdTool in ToolLoader().discover()
    assert ObdTool.config_cls() is ObdConfig


async def test_readiness(connected):
    tool, transport = connected
    transport.responses.append("410182076521>")
    result = json.loads(await tool.execute("readiness"))["readiness"][0]
    assert result["mil_on"] and result["stored_dtc_count"] == 2


@pytest.mark.parametrize(
    "response",
    [
        "014\r0:490201314434\r1:47503030523535\r2:42313233343536>",
        "49020100000031\r49020244344750\r49020330305235\r49020435423132\r49020533343536>",
    ],
)
async def test_vin(connected, response):
    tool, transport = connected
    transport.responses.append(response)
    assert json.loads(await tool.execute("vin"))["vin"] == "1D4GP00R55B123456"


async def test_timeout_closes(connected):
    tool, transport = connected
    transport.responses.append(TimeoutError("prompt timeout"))
    assert "timeout" in json.loads(await tool.execute("live"))["error"]
    assert transport.closed
    assert not json.loads(await tool.execute("status"))["connected"]


async def test_initialization_failure_closes():
    transport = MockTransport(["?>"])
    tool = ObdTool(transport_factory=lambda _: transport)
    assert "error" in json.loads(await tool.execute("connect", device="OBD"))
    assert transport.closed
    assert not json.loads(await tool.execute("status"))["connected"]


async def test_idle_timeout():
    transport = MockTransport(["ELM327>"] + ["OK>"] * 4)
    tool = ObdTool(ObdConfig(idle_timeout_s=0.01), transport_factory=lambda _: transport)
    await tool.execute("connect", device="OBD")
    await asyncio.sleep(0.04)
    assert transport.closed
    assert not json.loads(await tool.execute("status"))["connected"]
    await tool.close()


async def test_reconnect_closes_previous(connected):
    tool, old = connected
    new = MockTransport(["ELM327>"] + ["OK>"] * 4)
    tool._factory = lambda _: new
    assert json.loads(await tool.execute("connect", device="new"))["connected"]
    assert old.closed and not new.closed
    await tool.close()
    assert new.closed


async def test_cancelled_action_waits_for_worker_and_closes(connected):
    import threading

    tool, transport = connected
    started = threading.Event()
    released = threading.Event()

    def blocked_read(delimiter=">", timeout=10):
        started.set()
        assert released.wait(1)
        return "410C1AF8>"

    transport.read_until = blocked_read
    task = asyncio.create_task(tool.execute("live", pids=["0C"]))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.closed
    assert not json.loads(await tool.execute("status"))["connected"]
