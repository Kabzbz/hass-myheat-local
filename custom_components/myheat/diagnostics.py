"""Diagnostics download (Settings → Devices & services → MyHeat → ⋮)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import (
    CONF_API_KEY,
    CONF_DEVICE_KEY,
    CONF_LOCAL_LOGIN,
    CONF_LOCAL_PASSWORD,
    CONF_USERNAME,
    VERSION,
)
from .coordinator import MhConfigEntry

# Credentials and anything that identifies the device or the home.
TO_REDACT = {
    CONF_API_KEY,
    CONF_USERNAME,
    CONF_LOCAL_LOGIN,
    CONF_LOCAL_PASSWORD,
    CONF_DEVICE_KEY,
    "serial",
    "regkey",
    "wifiSsid",
    "city",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MhConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    burner = coordinator.burner
    return {
        "integration_version": VERSION,
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "active_source": coordinator.active_source,
        "local_enabled": coordinator.local_enabled,
        "local_only": coordinator.local_only,
        "last_update_success": coordinator.last_update_success,
        "update_interval_s": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
        "burner_poller": None
        if burner is None
        else {
            "interval_s": burner.interval_seconds,
            "last_update_success": burner.last_update_success,
            "data": async_redact_data(burner.data or {}, TO_REDACT),
        },
    }
