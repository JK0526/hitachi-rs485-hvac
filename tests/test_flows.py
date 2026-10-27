import copy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hitachi_rs485.config_flow import (
    HitachiConfigFlow,
    HitachiOptionsFlow,
    valid_host,
    validate_gateway,
)
from custom_components.hitachi_rs485.const import DOMAIN
from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.models import Device
from custom_components.hitachi_rs485.scanner import ScanResult
from custom_components.hitachi_rs485.transport import GatewayError


@pytest.fixture
def flow(hass):
    f = HitachiConfigFlow()
    f.hass = hass
    f.handler = DOMAIN
    f.flow_id = "flow"
    f.context = {"source": "user"}
    return f


@pytest.fixture
def options(hass, entry):
    f = HitachiOptionsFlow()
    f.hass = hass
    f.handler = entry.entry_id
    f.flow_id = "options"
    return f


@pytest.mark.parametrize(
    "host,expected",
    [("  TEST.local. ", "test.local"), ("192.168.1.2", "192.168.1.2"), ("::1", "::1")],
)
def test_host(host, expected):
    assert valid_host(host) == expected


@pytest.mark.parametrize(
    "host", ["", "bad host", "-bad", "x" * 64 + ".local", "x" * 254]
)
def test_invalid_host(host):
    with pytest.raises(ValueError):
        valid_host(host)


async def test_gateway(gateway):
    assert await validate_gateway({"host": "127.0.0.1", "port": gateway.port}) == {
        "host": "127.0.0.1",
        "port": gateway.port,
    }
    with (
        patch(
            "custom_components.hitachi_rs485.config_flow.Transport.connect",
            side_effect=GatewayError,
        ),
        patch(
            "custom_components.hitachi_rs485.config_flow.Transport.close",
            new=AsyncMock(),
        ) as close,
    ):
        with pytest.raises(GatewayError):
            await validate_gateway({"host": "test", "port": 502})
        close.assert_awaited_once()


async def test_new_flow_full(flow, device):
    assert (await flow.async_step_user())["step_id"] == "user"
    with (
        patch(
            "custom_components.hitachi_rs485.config_flow.validate_gateway",
            new=AsyncMock(return_value={"host": "test", "port": 502}),
        ),
        patch(
            "custom_components.hitachi_rs485.config_flow.scan",
            new=AsyncMock(
                return_value=[
                    ScanResult(device, True),
                    ScanResult(Device(3, family="dehumidifier"), False),
                ]
            ),
        ) as scanner,
    ):
        result = await flow.async_step_user({"host": "test", "port": 502})
        assert result["step_id"] == "scan_range"
        scanner.assert_not_awaited()
        result = await flow.async_step_scan_range({"scan_start": 2, "scan_end": 1})
        assert result["errors"] == {"base": "invalid_range"}
        with (
            patch.object(flow, "async_update_progress") as progress,
            patch.object(flow, "async_notify_flow_changed"),
        ):
            flow._scan_progress(1, 2, 1)
            progress.assert_called_once_with(0.5)
        result = await flow.async_step_scan_range({"scan_start": 1, "scan_end": 3})
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        await flow._scan_task
        result = await flow.async_step_scan()
        assert result["step_id"] == "select"
        result = await flow.async_step_select()
        assert "dehumidifier" in result["description_placeholders"]["found"]
        assert (await flow.async_step_select({"devices": []}))["errors"]
        assert (await flow.async_step_select({"devices": ["3"]}))["errors"]
        assert (await flow.async_step_select({"devices": ["1"]}))[
            "step_id"
        ] == "summary"
        result = await flow.async_step_confirm()
        assert result["data"] == {"host": "test", "port": 502}
        assert result["options"]["devices"] == [device.as_dict()]
    assert (await flow.async_step_cancel())["reason"] == "cancelled"
    assert isinstance(
        HitachiConfigFlow.async_get_options_flow(None), HitachiOptionsFlow
    )


async def test_endpoint_errors_duplicate(flow, entry):
    for error, code in (
        (ValueError(), "invalid_host"),
        (GatewayError(), "cannot_connect"),
    ):
        with patch(
            "custom_components.hitachi_rs485.config_flow.validate_gateway",
            side_effect=error,
        ):
            assert (await flow.async_step_user({"host": "x", "port": 502}))[
                "errors"
            ] == {"base": code}
    with patch(
        "custom_components.hitachi_rs485.config_flow.validate_gateway",
        new=AsyncMock(return_value=dict(entry.data)),
    ):
        assert (await flow.async_step_user(dict(entry.data)))[
            "reason"
        ] == "already_configured"


async def test_scan_failure_and_empty(flow):
    flow._gateway = {"host": "test", "port": 502}
    with patch(
        "custom_components.hitachi_rs485.config_flow.scan", side_effect=GatewayError
    ):
        await flow.async_step_scan()
        try:
            await flow._scan_task
        except GatewayError:
            pass
        assert (await flow.async_step_scan())["step_id"] == "scan_failed"
    assert (await flow.async_step_scan_failed())["menu_options"] == [
        "scan_range",
        "cancel",
    ]
    result = await flow.async_step_select()
    assert result["description_placeholders"]["found"] == "—"


async def test_options_staging_manage_cancel_confirm(options, entry, device):
    original = copy.deepcopy(dict(entry.options))
    assert (await options.async_step_init())["step_id"] == "menu"
    assert (await options.async_step_manage())["step_id"] == "manage"
    assert (await options.async_step_manage({"slave": "9"}))["errors"]
    assert (await options.async_step_manage({"slave": "1"}))["step_id"] == "device"
    assert (await options.async_step_device({"heat": "unsupported", "remove": False}))[
        "step_id"
    ] == "summary"
    assert entry.options == original
    assert (await options.async_step_cancel())["reason"] == "cancelled"
    await options.async_step_init()
    await options.async_step_manage({"slave": "1"})
    await options.async_step_device({"heat": "supported", "remove": False})
    result = await options.async_step_confirm()
    assert result["data"]["devices"][0]["heat"] == "supported"
    assert entry.options == original
    await options.async_step_init()
    await options.async_step_manage({"slave": "1"})
    await options.async_step_device({"heat": "auto", "remove": True})
    assert (await options.async_step_confirm())["data"]["devices"] == []


async def test_options_settings(options, entry):
    await options.async_step_init()
    assert (await options.async_step_communication())["step_id"] == "communication"
    for fast, slow, start, end in ((0.1, 30, 1, 2), (2, 1, 1, 2), (2, 30, 2, 1)):
        result = await options.async_step_communication(
            {
                "fast_poll_interval": fast,
                "slow_poll_interval": slow,
                "scan_start": start,
                "scan_end": end,
            }
        )
        assert result["errors"] == {"base": "invalid_settings"}
    result = await options.async_step_communication(
        {
            "fast_poll_interval": 0.5,
            "slow_poll_interval": 10.0,
            "scan_start": 1,
            "scan_end": 10,
        }
    )
    assert result["step_id"] == "summary"
    result = await options.async_step_confirm()
    assert (
        result["data"]["fast_poll_interval"] == 0.5
        and entry.options["fast_poll_interval"] == 0
    )


async def test_options_scan_shared_transport(hass, options, entry, device, gateway):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0, 30)
    entry.runtime_data = hub
    await options.async_step_init()
    options._draft["scan_end"] = 2
    gateway.absent = {1}
    try:
        # Suppress progress notifications for this directly exercised flow instance.
        with (
            patch.object(options, "async_update_progress"),
            patch.object(options, "async_notify_flow_changed"),
        ):
            await options.async_step_scan()
            await options._scan_task
        await options.async_step_scan()
        result = await options.async_step_select()
        assert result["description_placeholders"]["missing"] == "1"
        await options.async_step_select({"devices": ["2"]})
        result = await options.async_step_confirm()
        assert [d["slave"] for d in result["data"]["devices"]] == [1, 2]
        assert (
            gateway.connections == 2
        )  # Same transport reconnects after the absent slave timeout.
    finally:
        await hub.stop()
    del entry.runtime_data
    with pytest.raises(GatewayError):
        await options._run_scan()


async def test_reconfigure(hass, entry):
    old_options = copy.deepcopy(dict(entry.options))
    with (
        patch(
            "custom_components.hitachi_rs485.config_flow.validate_gateway",
            new=AsyncMock(
                side_effect=[
                    GatewayError(),
                    ValueError(),
                    {"host": "new.local", "port": 503},
                ]
            ),
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
        patch("homeassistant.config_entries.async_process_deps_reqs", new=AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        assert result["step_id"] == "reconfigure"
        flow_id = result["flow_id"]
        assert (
            await hass.config_entries.flow.async_configure(
                flow_id, {"host": "new.local", "port": 503}
            )
        )["errors"] == {"base": "cannot_connect"}
        assert (
            await hass.config_entries.flow.async_configure(
                flow_id, {"host": "bad host", "port": 503}
            )
        )["errors"] == {"base": "invalid_host"}
        assert (
            await hass.config_entries.flow.async_configure(
                flow_id, {"host": "new.local", "port": 503}
            )
        )["reason"] == "reconfigure_successful"
        reload.assert_called_once_with(entry.entry_id)
    assert entry.options == old_options
    assert entry.data == {"host": "new.local", "port": 503}
