"""
Custom integration to integrate MyHeat with Home Assistant.

For more details about this integration, please refer to
https://github.com/vooon/hass-myheat
"""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import MhApiClient
from .const import (
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_LOCAL_ENABLED,
    CONF_LOCAL_HOST,
    CONF_LOCAL_LOGIN,
    CONF_LOCAL_ONLY,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_PROTOCOL,
    CONF_LOCAL_TIMEOUT,
    CONF_USERNAME,
    DEFAULT_LOCAL_PROTOCOL,
    DEFAULT_LOCAL_TIMEOUT,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)
from homeassistant.helpers import entity_registry as er
from .const import CONF_NAME  # noqa: F401
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .local_api import MhLocalApiClient
from .services import async_setup_services

_LOGGER: logging.Logger = logging.getLogger(__package__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up this integration using YAML is not supported."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MhConfigEntry):
    """Set up this integration using UI."""
    if DOMAIN not in hass.data:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    session = async_get_clientsession(hass)

    # Local client (optional)
    local_client: MhLocalApiClient | None = None
    local_enabled = bool(entry.data.get(CONF_LOCAL_ENABLED)) or bool(
        entry.data.get(CONF_LOCAL_ONLY)
    )
    local_only = bool(entry.data.get(CONF_LOCAL_ONLY))
    if local_enabled:
        host = entry.data.get(CONF_LOCAL_HOST)
        if host:
            local_client = MhLocalApiClient(
                host=host,
                login=entry.data.get(CONF_LOCAL_LOGIN, ""),
                password=entry.data.get(CONF_LOCAL_PASSWORD, ""),
                session=session,
                protocol=entry.data.get(CONF_LOCAL_PROTOCOL, DEFAULT_LOCAL_PROTOCOL),
                timeout=int(entry.data.get(CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT)),
            )

    # Cloud client (optional in local-only mode)
    cloud_client: MhApiClient | None = None
    if not local_only:
        cloud_client = MhApiClient(
            username=entry.data.get(CONF_USERNAME),
            api_key=entry.data.get(CONF_API_KEY),
            device_id=entry.data.get(CONF_DEVICE_ID),
            session=session,
            local_client=local_client,
            local_only=False,
        )
    else:
        # In local-only mode we still expose an MhApiClient (for write routing),
        # but it never touches the cloud.
        cloud_client = MhApiClient(
            session=session,
            local_client=local_client,
            local_only=True,
        )

    coordinator = MhDataUpdateCoordinator(
        hass,
        entry=entry,
        client=cloud_client,
        local_client=local_client,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.add_update_listener(async_reload_entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MhConfigEntry) -> bool:
    """Handle removal of an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: MhConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: MhConfigEntry) -> bool:
    """Migrate v1 entries (entry_id-based unique_ids) to v2 (device-key-based).

    Computes a stable device_key for the entry (cloud device_id, or local
    serial fetched on-the-fly, or fallback to host) and rewrites every entity
    unique_id from the legacy ``<entry_id>...`` form to ``<device_key>...``.

    HA's entity registry preserves entity_id across unique_id changes, so all
    existing automations, dashboards and history keep working.
    """
    if entry.version >= 2:
        return True

    _LOGGER.info("Migrating MyHeat entry %s from v1 to v2", entry.entry_id)

    # 1. Pick the new device_key
    if entry.data.get(CONF_DEVICE_KEY):
        device_key = str(entry.data[CONF_DEVICE_KEY])
    elif entry.data.get(CONF_DEVICE_ID):
        device_key = str(entry.data[CONF_DEVICE_ID])
    else:
        # local-only entry — fetch serial; fall back to host
        device_key = None
        if entry.data.get(CONF_LOCAL_HOST):
            try:
                session = async_get_clientsession(hass)
                lc = MhLocalApiClient(
                    host=entry.data[CONF_LOCAL_HOST],
                    login=entry.data.get(CONF_LOCAL_LOGIN, ""),
                    password=entry.data.get(CONF_LOCAL_PASSWORD, ""),
                    session=session,
                    protocol=entry.data.get(CONF_LOCAL_PROTOCOL, DEFAULT_LOCAL_PROTOCOL),
                    timeout=int(
                        entry.data.get(CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT)
                    ),
                )
                state = await lc.async_get_state()
                serial = state.get("serial")
                if serial:
                    device_key = f"local_{serial}"
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Migration: could not fetch local serial (%s); using host fallback",
                    exc,
                )
        if not device_key:
            device_key = f"local_host_{entry.data.get(CONF_LOCAL_HOST, entry.entry_id)}"

    # 2. Rewrite entity unique_ids
    registry = er.async_get(hass)
    old_prefix = entry.entry_id
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    renamed = 0
    for ent in entries:
        if ent.unique_id.startswith(old_prefix):
            new_uid = device_key + ent.unique_id[len(old_prefix):]
            try:
                registry.async_update_entity(ent.entity_id, new_unique_id=new_uid)
                renamed += 1
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Migration: failed to rename %s (%s)", ent.entity_id, exc
                )

    # 3. Persist device_key in entry.data + bump version
    new_data = {**entry.data, CONF_DEVICE_KEY: device_key}
    hass.config_entries.async_update_entry(entry, data=new_data, version=2)

    _LOGGER.info(
        "MyHeat entry migrated: device_key=%s, renamed %d entities",
        device_key,
        renamed,
    )
    return True
