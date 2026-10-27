"""Native gateway setup, explicit read-only discovery and staged device management."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import re
from dataclasses import replace
from typing import Any
from uuid import uuid4

import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DEFAULT_PORT, DOMAIN
from .coordinator import valid_poll_interval
from .models import HeatSupport, devices_from_options
from .scanner import ScanResult, scan, validate_range
from .transport import GatewayError, Transport

DEFAULTS = {
    "fast_poll_interval": 2.0,
    "slow_poll_interval": 30.0,
    "scan_start": 1,
    "scan_end": 247,
    "devices": [],
}


def valid_host(value: str) -> str:
    value = value.strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if (
            not value
            or len(value) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p)
                for p in value.split(".")
            )
        ):
            raise ValueError("Invalid host") from None
    return value


def gateway_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("host", default=defaults.get("host", "")): str,
            vol.Required("port", default=defaults.get("port", DEFAULT_PORT)): vol.All(
                int, vol.Range(min=1, max=65535)
            ),
        }
    )


async def validate_gateway(data: dict[str, Any]) -> dict[str, Any]:
    result = {"host": valid_host(data["host"]), "port": data["port"]}
    transport = Transport(**result)
    try:
        await transport.connect()
    finally:
        await transport.close()
    return result


def select(
    options: list[str] | list[dict[str, str]],
    multiple: bool = False,
    translation_key: str | None = None,
) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            **(
                {"translation_key": translation_key}
                if translation_key is not None
                else {}
            ),
            multiple=multiple,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class DiscoveryFlow:
    """Changes stay in a private draft until the summary is confirmed."""

    def __init__(self) -> None:
        super().__init__()
        self._gateway = {}
        self._draft = copy.deepcopy(DEFAULTS)
        self._found = {}
        self._scan_task = None
        self._progress = {"current": "0", "total": "247", "found": "0"}

    def _scan_progress(self, current: int, total: int, found: int) -> None:
        self._progress = {
            "current": str(current),
            "total": str(total),
            "found": str(found),
        }
        self.async_update_progress(current / total)
        self.async_notify_flow_changed()

    async def _run_scan(self) -> list[ScanResult]:
        await asyncio.sleep(0)
        args = self._draft["scan_start"], self._draft["scan_end"], self._scan_progress
        if isinstance(self, HitachiOptionsFlow):
            if not hasattr(self.config_entry, "runtime_data"):
                raise GatewayError("Load the entry before scanning")
            return await self.config_entry.runtime_data.scan(*args)
        transport = Transport(**self._gateway)
        try:
            return await scan(transport, *args)
        finally:
            await transport.close()

    async def async_step_scan_range(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                validate_range(user_input["scan_start"], user_input["scan_end"])
            except ValueError:
                errors["base"] = "invalid_range"
            else:
                self._draft.update(user_input)
                return await self.async_step_scan()
        return self.async_show_form(
            step_id="scan_range",
            data_schema=vol.Schema(
                {
                    vol.Required(k, default=self._draft[k]): vol.All(
                        int, vol.Range(min=1, max=247)
                    )
                    for k in ("scan_start", "scan_end")
                }
            ),
            errors=errors,
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                self._run_scan(), "hitachi-scan"
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan",
                progress_action="scan",
                description_placeholders=self._progress,
                progress_task=self._scan_task,
            )
        try:
            self._found = {r.slave: r for r in self._scan_task.result()}
        except GatewayError:
            next_step = "scan_failed"
        else:
            next_step = "select"
        self._scan_task = None
        return self.async_show_progress_done(next_step_id=next_step)

    async def async_step_scan_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="scan_failed", menu_options=["scan_range", "cancel"]
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        existing = {d["slave"] for d in self._draft["devices"]}
        candidates = {
            s: r for s, r in self._found.items() if r.supported and s not in existing
        }
        errors = {}
        if user_input is not None:
            selected = user_input.get("devices", [])
            if not selected or any(int(s) not in candidates for s in selected):
                errors["base"] = "no_devices"
            else:
                self._draft["devices"].extend(
                    candidates[int(s)].device.as_dict() for s in selected
                )
                return await self.async_step_summary()
        found = (
            "\n".join(
                f"#{s}: {r.device.interface} / {r.device.family}"
                for s, r in self._found.items()
            )
            or "—"
        )
        return self.async_show_form(
            step_id="select",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("devices", default=[]): select(
                        [
                            {
                                "value": str(s),
                                "label": f"Slave #{s} · RC-R02XT {r.device.series}",
                            }
                            for s, r in candidates.items()
                        ],
                        True,
                    )
                }
            ),
            description_placeholders={
                "found": found,
                "existing": ", ".join(map(str, sorted(existing & self._found.keys())))
                or "—",
                "missing": ", ".join(map(str, sorted(existing - self._found.keys())))
                or "—",
            },
        )

    async def async_step_summary(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="summary",
            menu_options=["confirm", "back", "cancel"],
            description_placeholders={
                "summary": "\n".join(
                    f"Slave #{d['slave']} · {d['heat']}" for d in self._draft["devices"]
                )
                + f"\nFast: {self._draft['fast_poll_interval']}s / Slow: {self._draft['slow_poll_interval']}s"
            },
        )

    async def async_step_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if isinstance(self, HitachiOptionsFlow):
            return await self.async_step_menu()
        return await self.async_step_select()

    async def async_step_cancel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="cancelled")


class HitachiConfigFlow(DiscoveryFlow, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HitachiOptionsFlow:
        return HitachiOptionsFlow()

    async def _endpoint(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                gateway = await validate_gateway(user_input)
            except ValueError:
                errors["base"] = "invalid_host"
            except GatewayError:
                errors["base"] = "cannot_connect"
            else:
                for entry in self._async_current_entries():
                    if dict(entry.data) == gateway and (
                        step_id != "reconfigure"
                        or entry.entry_id != self.context["entry_id"]
                    ):
                        return self.async_abort(reason="already_configured")
                if step_id == "reconfigure":
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=gateway
                    )
                self._gateway = gateway
                return await self.async_step_scan_range()
        defaults = (
            self._get_reconfigure_entry().data
            if step_id == "reconfigure"
            else self._gateway
        )
        return self.async_show_form(
            step_id=step_id, data_schema=gateway_schema(defaults), errors=errors
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._endpoint("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._endpoint("reconfigure", user_input)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(uuid4().hex)
        return self.async_create_entry(
            title=f"Hitachi RS485 · {self._gateway['host']}",
            data=self._gateway,
            options=self._draft,
        )


class HitachiOptionsFlow(DiscoveryFlow, config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = {
            **copy.deepcopy(DEFAULTS),
            **copy.deepcopy(dict(self.config_entry.options)),
        }
        return await self.async_step_menu()

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="menu",
            menu_options=["scan_range", "manage", "communication", "summary", "cancel"],
        )

    async def async_step_manage(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            slave = int(user_input["slave"])
            if slave in {d["slave"] for d in self._draft["devices"]}:
                self._managed_slave = slave
                return await self.async_step_device()
            errors["base"] = "no_devices"
        return self.async_show_form(
            step_id="manage",
            data_schema=vol.Schema(
                {
                    vol.Required("slave"): select(
                        [
                            {"value": str(d["slave"]), "label": f"Slave #{d['slave']}"}
                            for d in self._draft["devices"]
                        ]
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        device = next(
            d
            for d in devices_from_options(self._draft)
            if d.slave == self._managed_slave
        )
        if user_input is not None:
            if user_input.get("remove", False):
                self._draft["devices"] = [
                    d for d in self._draft["devices"] if d["slave"] != device.slave
                ]
            else:
                updated = replace(device, heat=HeatSupport(user_input["heat"]))
                self._draft["devices"] = [
                    updated.as_dict() if d["slave"] == device.slave else d
                    for d in self._draft["devices"]
                ]
            return await self.async_step_summary()
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required("heat", default=device.heat.value): select(
                        [h.value for h in HeatSupport]
                    ),
                    vol.Required("remove", default=False): bool,
                }
            ),
            description_placeholders={"slave": str(device.slave)},
        )

    async def async_step_communication(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                validate_range(user_input["scan_start"], user_input["scan_end"])
                if not valid_poll_interval(
                    user_input["fast_poll_interval"]
                ) or not valid_poll_interval(user_input["slow_poll_interval"], 10):
                    raise ValueError("Invalid interval")
            except ValueError:
                errors["base"] = "invalid_settings"
            else:
                self._draft.update(user_input)
                return await self.async_step_summary()
        return self.async_show_form(
            step_id="communication",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(k, default=self._draft[k]): float
                    if "interval" in k
                    else int
                    for k in (
                        "fast_poll_interval",
                        "slow_poll_interval",
                        "scan_start",
                        "scan_end",
                    )
                }
            ),
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=copy.deepcopy(self._draft))
