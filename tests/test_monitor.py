from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hitachi_rs485.const import DOMAIN, VERSION
from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.monitor import (
    async_register_monitor,
    websocket_clear,
    websocket_list,
    websocket_poll,
    websocket_subscribe,
)

HANDLERS = (websocket_list, websocket_subscribe, websocket_clear, websocket_poll)


@pytest.fixture
async def monitored(hass, entry, device):
    hub = Hub.create(hass, "private-host", 502, (device,), 2, 30)
    entry.runtime_data = hub
    yield hub
    await hub.stop()


@pytest.fixture
async def ws(hass, hass_ws_client, entry, monitored):
    assert await async_setup_component(hass, "websocket_api", {})
    for handler in HANDLERS:
        websocket_api.async_register_command(hass, handler)
    client = await hass_ws_client(hass)
    yield client
    await client.close()


async def test_traces_subscription_clear_reload(hass, entry, monitored, ws):
    monitored.transport.submit = AsyncMock(
        side_effect=AssertionError("Monitor must not generate bus traffic")
    )
    await ws.send_json({"id": 1, "type": f"{DOMAIN}/monitor/list"})
    assert (await ws.receive_json())["result"] == [
        {"entry_id": entry.entry_id, "title": entry.title}
    ]
    monitored.transport.trace.record("TX", bytes.fromhex("010300400005"), source="poll")
    await ws.send_json(
        {"id": 2, "type": f"{DOMAIN}/monitor/subscribe", "entry_id": entry.entry_id}
    )
    assert (await ws.receive_json())["success"]
    snap = (await ws.receive_json())["event"]
    assert snap["entries"][0]["raw_hex"] == "01 03 00 40 00 05"
    assert (
        snap["polling_state"] == "running" and snap["coordinator"]["queue_depth"] == 0
    )
    await ws.send_json(
        {"id": 3, "type": f"{DOMAIN}/monitor/clear", "entry_id": entry.entry_id}
    )
    assert (await ws.receive_json())["success"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert (await ws.receive_json())["event"]["entries"] == []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()
    del entry.runtime_data
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert (await ws.receive_json())["event"]["polling_state"] == "unloaded"
    entry.runtime_data = monitored
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4))
    await hass.async_block_till_done()
    assert (await ws.receive_json())["event"]["poll_interval"] == 2
    await ws.send_json({"id": 4, "type": "unsubscribe_events", "subscription": 2})
    assert (await ws.receive_json())["success"]
    monitored.transport.submit.assert_not_awaited()


@pytest.mark.parametrize("command", ["subscribe", "clear", "poll"])
async def test_missing(ws, command):
    msg = {"id": 1, "type": f"{DOMAIN}/monitor/{command}", "entry_id": "missing"}
    if command == "poll":
        msg["paused"] = True
    await ws.send_json(msg)
    assert (await ws.receive_json())["error"]["code"] == "not_loaded"


@pytest.mark.parametrize("command", ["list", "subscribe", "clear", "poll"])
async def test_admin(hass, hass_ws_client, hass_read_only_access_token, command):
    assert await async_setup_component(hass, "websocket_api", {})
    for h in HANDLERS:
        websocket_api.async_register_command(hass, h)
    client = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    msg = {"id": 1, "type": f"{DOMAIN}/monitor/{command}"}
    if command != "list":
        msg["entry_id"] = "any"
    if command == "poll":
        msg["paused"] = True
    await client.send_json(msg)
    assert (await client.receive_json())["error"]["code"] == "unauthorized"
    await client.close()


async def test_pause_and_zero(monitored, entry, ws):
    for i, paused in enumerate((True, False), 1):
        await ws.send_json(
            {
                "id": i,
                "type": f"{DOMAIN}/monitor/poll",
                "entry_id": entry.entry_id,
                "paused": paused,
            }
        )
        assert (await ws.receive_json())["success"]
        assert monitored.coordinator.runtime_poll_paused == paused
    monitored.coordinator.fast_poll_interval = 0
    await ws.send_json(
        {
            "id": 3,
            "type": f"{DOMAIN}/monitor/poll",
            "entry_id": entry.entry_id,
            "paused": False,
        }
    )
    assert (await ws.receive_json())["error"]["code"] == "polling_disabled"
    await ws.send_json(
        {
            "id": 4,
            "type": f"{DOMAIN}/monitor/clear",
            "entry_id": entry.entry_id,
            "register": 56,
            "value": 0,
        }
    )
    assert not (await ws.receive_json())["success"]


async def test_register_panel(hass):
    hass.http = Mock(async_register_static_paths=AsyncMock())
    with patch(
        "custom_components.hitachi_rs485.monitor.websocket_api.async_register_command"
    ) as register:
        await async_register_monitor(hass)
    assert register.call_count == 4
    panel = hass.data[DATA_PANELS]["hitachi-rs485-monitor"]
    assert panel.require_admin
    assert (
        panel.config["_panel_custom"]["module_url"] == f"/{DOMAIN}/monitor-{VERSION}.js"
    )


async def test_setup_registers(hass):
    from custom_components.hitachi_rs485 import async_setup

    with patch(
        "custom_components.hitachi_rs485.monitor.async_register_monitor",
        new=AsyncMock(),
    ) as register:
        assert await async_setup(hass, {})
        register.assert_awaited_once_with(hass)
