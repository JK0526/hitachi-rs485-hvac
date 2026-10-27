"""Small redacted diagnostic snapshot; never includes raw endpoint identifiers."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .const import VERSION
from .hub import Hub

if TYPE_CHECKING:
    from . import HitachiConfigEntry


def snapshot(hub: Hub) -> dict[str, Any]:
    c, t = hub.coordinator, hub.transport
    return {
        "integration": {"version": VERSION},
        "gateway": {"host": "**REDACTED**", "port": t.port, "connected": t.connected},
        "coordinator": {
            "fast_poll_interval": c.fast_poll_interval,
            "slow_poll_interval": c.slow_poll_interval,
            "runtime_poll_paused": c.runtime_poll_paused,
            "polling_paused": c.polling_paused,
            "last_fast_poll_round_ms": c.last_fast_poll_round_ms,
            "average_fast_poll_round_ms": c.average_fast_poll_round_ms,
            "last_poll_round_ms": c.last_fast_poll_round_ms,
            "average_poll_round_ms": c.average_fast_poll_round_ms,
            "queue_depth": t.queue_depth,
            "queue_peak": t.statistics.queue_peak,
            "configured_slaves": len(c.devices),
            "available_slaves": sum(
                c.gateway_available and s.available for s in c.states.values()
            ),
        },
        "statistics": asdict(t.statistics),
        "devices": [
            {
                **d.as_dict(),
                "model": d.interface,
                "available": c.gateway_available and c.states[d.slave].available,
                "consecutive_failures": c.states[d.slave].consecutive_failures,
                "raw": dict(c.states[d.slave].raw),
                "optional_unavailable": sorted(c.states[d.slave].optional_unavailable),
                "communication_issue": c.states[d.slave].communication_issue,
            }
            for d in c.devices
        ],
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HitachiConfigEntry
) -> dict[str, Any]:
    return snapshot(entry.runtime_data)
