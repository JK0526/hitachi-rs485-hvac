"""End-to-end native HA setup and service dispatch, without a real HVAC device."""

from unittest.mock import AsyncMock, patch

from custom_components.hitachi_rs485.const import DOMAIN


async def test_native_entry_poll_zero_controls_and_unload(hass, entry, gateway):
    hass.config_entries.async_update_entry(
        entry, data={"host": "127.0.0.1", "port": gateway.port}
    )
    # The monitor backend/panel is tested separately; isolate its frontend dependencies here.
    with patch(
        "custom_components.hitachi_rs485.monitor.async_register_monitor",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    climate = next(s for s in hass.states.async_all("climate"))
    assert climate.state == "unknown"
    assert gateway.requests == []
    await hass.services.async_call(
        "climate", "turn_on", {"entity_id": climate.entity_id}, blocking=True
    )
    assert hass.states.get(climate.entity_id).state == "cool"
    assert gateway.requests[0][1] == 6
    assert gateway.requests[1][2:6] == bytes.fromhex("00400005")
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate.entity_id, "temperature": 27},
        blocking=True,
    )
    assert hass.states.get(climate.entity_id).attributes["temperature"] == 27
    runtime = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert runtime.transport._worker is None
    assert not hasattr(entry, "runtime_data")


async def test_native_user_flow_connection_to_scan_page(hass, gateway):
    # Native flow manager, real endpoint validation and dependency discovery.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": gateway.port}
    )
    assert result["step_id"] == "scan_range"
    assert not gateway.requests
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_native_flow_discovers_selects_and_creates(hass, gateway):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": gateway.port}
    )
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"scan_start": 1, "scan_end": 1}
    )
    assert result["type"] == "progress"
    flow = hass.config_entries.flow._progress[flow_id]
    await flow._scan_task
    result = await hass.config_entries.flow.async_configure(flow_id)
    if result["type"] == "progress_done":
        result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["step_id"] == "select"
    result = await hass.config_entries.flow.async_configure(flow_id, {"devices": ["1"]})
    assert result["step_id"] == "summary"
    with patch(
        "custom_components.hitachi_rs485.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"next_step_id": "confirm"}
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.options["devices"][0]["slave"] == 1
    assert "energy" in entry.options["devices"][0]["capabilities"]
