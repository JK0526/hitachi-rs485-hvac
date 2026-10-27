"""Regression tests for staged rescan, conservative probes and HA reload."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.hitachi_rs485.const import DOMAIN
from custom_components.hitachi_rs485.models import Device, HeatSupport
from custom_components.hitachi_rs485.profiles import OPTIONAL_REGISTERS
from custom_components.hitachi_rs485.scanner import ProbeStatus, ScanResult, scan
from custom_components.hitachi_rs485.transport import Transport


@pytest.mark.parametrize("outcome", ["timeout", "crc", 1, 3, 4, 6])
@pytest.mark.parametrize("capability", list(OPTIONAL_REGISTERS))
async def test_scanner_distinguishes_transient_failure(gateway, capability, outcome):
    register = OPTIONAL_REGISTERS[capability]
    gateway.modes[(1, 3, register)] = outcome
    transport = Transport("127.0.0.1", gateway.port, timeout=0.1)
    try:
        result = (await scan(transport, 1, 1))[0]
        assert result.supported
        assert result.capability_probes[capability] == ProbeStatus.TRANSIENT_FAILURE
        assert capability not in result.device.capabilities
        assert all(request[1] == 3 for request in gateway.requests)
    finally:
        await transport.close()


@pytest.mark.parametrize("capability", list(OPTIONAL_REGISTERS))
async def test_scanner_identifies_explicit_unsupported(gateway, capability):
    gateway.modes[(1, 3, OPTIONAL_REGISTERS[capability])] = 2
    transport = Transport("127.0.0.1", gateway.port, timeout=0.1)
    try:
        result = (await scan(transport, 1, 1))[0]
        assert result.capability_probes[capability] == ProbeStatus.UNSUPPORTED
        assert capability not in result.device.capabilities
        assert all(
            result.capability_probes[other] == ProbeStatus.SUPPORTED
            for other in OPTIONAL_REGISTERS
            if other != capability
        )
    finally:
        await transport.close()


async def start_direct_rescan(options, results):
    async def delayed_scan():
        await asyncio.sleep(0)
        return results

    with patch.object(options, "_run_scan", side_effect=delayed_scan):
        progress = await options.async_step_scan()
        assert progress["type"] == "progress"
        await options._scan_task
        return await options.async_step_scan()


@pytest.fixture
def options_flow(hass, entry):
    from custom_components.hitachi_rs485.config_flow import HitachiOptionsFlow

    flow = HitachiOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.flow_id = "rescan"
    return flow


@pytest.mark.parametrize("heat", [HeatSupport.SUPPORTED, HeatSupport.UNSUPPORTED])
async def test_rescan_existing_updates_draft_metadata_and_preserves_heat(
    hass, entry, device, options_flow, heat
):
    old = replace(device, heat=heat, capabilities=(), series="OLD", capacity=1)
    options = {**dict(entry.options), "devices": [old.as_dict()]}
    hass.config_entries.async_update_entry(entry, options=options)
    saved = copy.deepcopy(dict(entry.options))
    await options_flow.async_step_init()
    new = replace(
        device,
        unit_type=4,
        capacity=42,
        series="NEW",
        saanet_version="3.0",
        identity={4: 0, 5: 4, 6: 42, 7: 0x4E45, 8: 0x5720, 9: 0x300},
        capabilities=("energy",),
        heat=HeatSupport.AUTO,
    )
    result = ScanResult(new, True, {"energy": ProbeStatus.SUPPORTED})
    assert (await start_direct_rescan(options_flow, [result]))["step_id"] == "no_new"
    staged = Device.from_dict(options_flow._draft["devices"][0])
    assert staged == replace(new, heat=heat)
    assert dict(entry.options) == saved
    page = await options_flow.async_step_no_new()
    assert page["description_placeholders"]["existing"] == "1"
    assert page["description_placeholders"]["missing"] == "—"
    assert "summary" in page["menu_options"]
    summary = await options_flow.async_step_summary()
    assert "energy" in summary["description_placeholders"]["summary"]
    assert (await options_flow.async_step_cancel())["reason"] == "cancelled"
    assert dict(entry.options) == saved


async def test_missing_and_unsupported_existing_remain_unchanged(
    hass, entry, device, options_flow
):
    missing = replace(device, slave=2, heat=HeatSupport.UNSUPPORTED)
    unsupported = replace(device, slave=3, heat=HeatSupport.SUPPORTED)
    original = [device.as_dict(), missing.as_dict(), unsupported.as_dict()]
    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), "devices": original}
    )
    await options_flow.async_step_init()
    unknown = Device(3, family="dehumidifier", interface="unknown")
    absent_result = ScanResult(unknown, False)
    updated = replace(device, series="SCANNED")
    assert (
        await start_direct_rescan(
            options_flow, [ScanResult(updated, True), absent_result]
        )
    )["step_id"] == "no_new"
    page = await options_flow.async_step_no_new()
    assert page["description_placeholders"]["missing"] == "2"
    assert "dehumidifier" in page["description_placeholders"]["found"]
    assert options_flow._draft["devices"][1:] == original[1:]
    assert entry.options["devices"] == original


@pytest.mark.parametrize("capability", list(OPTIONAL_REGISTERS))
@pytest.mark.parametrize(
    "status,retained",
    [(ProbeStatus.TRANSIENT_FAILURE, True), (ProbeStatus.UNSUPPORTED, False)],
)
async def test_existing_capability_retained_or_removed_by_explicit_result(
    hass, entry, device, options_flow, capability, status, retained
):
    old = replace(device, capabilities=(capability,), heat=HeatSupport.SUPPORTED)
    hass.config_entries.async_update_entry(
        entry, options={**dict(entry.options), "devices": [old.as_dict()]}
    )
    await options_flow.async_step_init()
    scanned = replace(device, capabilities=(), heat=HeatSupport.AUTO)
    result = ScanResult(scanned, True, {capability: status})
    assert (await start_direct_rescan(options_flow, [result]))["step_id"] == "no_new"
    staged = Device.from_dict(options_flow._draft["devices"][0])
    assert (capability in staged.capabilities) is retained
    assert staged.heat == HeatSupport.SUPPORTED
    assert Device.from_dict(entry.options["devices"][0]).capabilities == (capability,)
    data = (await options_flow.async_step_confirm())["data"]
    assert (capability in data["devices"][0]["capabilities"]) is retained


async def test_new_device_selection_is_optional_during_rescan(
    entry, device, options_flow
):
    await options_flow.async_step_init()
    result = ScanResult(replace(device, slave=2), True)
    assert (await start_direct_rescan(options_flow, [result]))["step_id"] == "select"
    assert (await options_flow.async_step_select({"devices": []}))[
        "step_id"
    ] == "summary"
    assert [d["slave"] for d in options_flow._draft["devices"]] == [1]
    assert len(entry.options["devices"]) == 1


async def test_rescan_failure_does_not_commit_partial_updates(entry, options_flow):
    from custom_components.hitachi_rs485.transport import GatewayError

    await options_flow.async_step_init()
    before = copy.deepcopy(options_flow._draft)

    async def failed_scan():
        await asyncio.sleep(0)
        raise GatewayError()

    with patch.object(options_flow, "_run_scan", side_effect=failed_scan):
        await options_flow.async_step_scan()
        with pytest.raises(GatewayError):
            await options_flow._scan_task
        assert (await options_flow.async_step_scan())["step_id"] == "scan_failed"
    assert options_flow._draft == before
    assert entry.options["devices"] == before["devices"]


async def test_native_confirm_reloads_existing_device_and_creates_energy(
    hass, entry, device, gateway
):
    old = replace(device, capabilities=(), heat=HeatSupport.UNSUPPORTED)
    hass.config_entries.async_update_entry(
        entry,
        data={"host": "127.0.0.1", "port": gateway.port},
        options={**dict(entry.options), "scan_end": 1, "devices": [old.as_dict()]},
    )
    with patch(
        "custom_components.hitachi_rs485.monitor.async_register_monitor",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    energy_uid = f"{entry.entry_id}_slave_1_energy"
    assert registry.async_get_entity_id("sensor", DOMAIN, energy_uid) is None
    original_runtime = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    assert result["step_id"] == "menu"
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "scan_range"}
    )
    assert result["step_id"] == "scan_range"
    result = await hass.config_entries.options.async_configure(
        flow_id, {"scan_start": 1, "scan_end": 1}
    )
    assert result["type"] == "progress"
    flow = hass.config_entries.options._progress[flow_id]
    await flow._scan_task
    result = await hass.config_entries.options.async_configure(flow_id)
    if result["type"] == "progress_done":
        result = await hass.config_entries.options.async_configure(flow_id)
    assert result["step_id"] == "no_new"
    assert entry.options["devices"][0] == old.as_dict()
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "summary"}
    )
    assert "energy" in result["description_placeholders"]["summary"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "confirm"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data is not original_runtime
    assert "energy" in entry.options["devices"][0]["capabilities"]
    assert entry.options["devices"][0]["heat"] == "unsupported"
    assert registry.async_get_entity_id("sensor", DOMAIN, energy_uid) is not None
    assert not original_runtime.transport.connected
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    "outcome,expected", [("timeout", True), ("crc", True), (2, False)]
)
async def test_native_rescan_capability_failures_apply_correct_policy(
    hass, entry, device, gateway, outcome, expected
):
    """Exercise the actual scanner, options manager, reload and entity registry."""
    old = replace(device, capabilities=("energy",), heat=HeatSupport.SUPPORTED)
    hass.config_entries.async_update_entry(
        entry,
        data={"host": "127.0.0.1", "port": gateway.port},
        options={**dict(entry.options), "scan_end": 1, "devices": [old.as_dict()]},
    )
    with patch(
        "custom_components.hitachi_rs485.monitor.async_register_monitor",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entry.runtime_data.transport.timeout = 0.1
    registry = er.async_get(hass)
    uid = f"{entry.entry_id}_slave_1_energy"
    original_entity_id = registry.async_get_entity_id("sensor", DOMAIN, uid)
    assert original_entity_id is not None
    gateway.modes[(1, 3, 0x38)] = outcome

    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "scan_range"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id, {"scan_start": 1, "scan_end": 1}
    )
    assert result["type"] == "progress"
    flow = hass.config_entries.options._progress[flow_id]
    await flow._scan_task
    result = await hass.config_entries.options.async_configure(flow_id)
    if result["type"] == "progress_done":
        result = await hass.config_entries.options.async_configure(flow_id)
    assert result["step_id"] == "no_new"
    assert ("energy" in flow._draft["devices"][0]["capabilities"]) is expected
    assert "energy" in entry.options["devices"][0]["capabilities"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "summary"}
    )
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "confirm"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ("energy" in entry.options["devices"][0]["capabilities"]) is expected
    assert entry.options["devices"][0]["heat"] == "supported"
    assert registry.async_get_entity_id("sensor", DOMAIN, uid) == original_entity_id
    # Unsupported entities retain their registry history, but are no longer loaded.
    state = hass.states.get(original_entity_id)
    assert state is not None and state.state == "unavailable"
    await entry.runtime_data.coordinator.async_poll_round()
    await entry.runtime_data.coordinator.async_poll_round(slow=True)
    state = hass.states.get(original_entity_id)
    if expected:
        assert state is not None and state.state == "unavailable"
        gateway.modes.clear()
        await entry.runtime_data.coordinator.async_poll_round(slow=True)
        assert hass.states.get(original_entity_id).state == "13.7"
    else:
        assert state is not None and state.state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_cancel_discards_refreshed_capability(
    hass, entry, device, gateway
):
    old = replace(device, capabilities=(), heat=HeatSupport.UNSUPPORTED)
    hass.config_entries.async_update_entry(
        entry,
        data={"host": "127.0.0.1", "port": gateway.port},
        options={**dict(entry.options), "scan_end": 1, "devices": [old.as_dict()]},
    )
    with patch(
        "custom_components.hitachi_rs485.monitor.async_register_monitor",
        new=AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    saved = copy.deepcopy(dict(entry.options))
    runtime = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow_id = result["flow_id"]
    await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "scan_range"}
    )
    await hass.config_entries.options.async_configure(
        flow_id, {"scan_start": 1, "scan_end": 1}
    )
    flow = hass.config_entries.options._progress[flow_id]
    await flow._scan_task
    result = await hass.config_entries.options.async_configure(flow_id)
    if result["type"] == "progress_done":
        result = await hass.config_entries.options.async_configure(flow_id)
    assert result["step_id"] == "no_new"
    assert "energy" in flow._draft["devices"][0]["capabilities"]
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "cancel"}
    )
    assert result["type"] == "abort" and result["reason"] == "cancelled"
    await hass.async_block_till_done()
    assert dict(entry.options) == saved
    assert entry.runtime_data is runtime
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_slave_1_energy"
        )
        is None
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
