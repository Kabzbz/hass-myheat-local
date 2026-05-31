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
