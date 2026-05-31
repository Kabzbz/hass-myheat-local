"""Adds config flow for MyHeat."""

import logging
from typing import Any

from homeassistant.config_entries import (
    CONN_CLASS_CLOUD_POLL,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
import voluptuous as vol

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
    CONF_LOCAL_POLL_INTERVAL,
    CONF_LOCAL_PROTOCOL,
    CONF_LOCAL_TIMEOUT,
    CONF_NAME,
    CONF_USERNAME,
    DEFAULT_LOCAL_HOST,
    DEFAULT_LOCAL_LOGIN,
    DEFAULT_LOCAL_PASSWORD,
    DEFAULT_LOCAL_POLL_INTERVAL,
    DEFAULT_LOCAL_PROTOCOL,
    DEFAULT_LOCAL_TIMEOUT,
    DOMAIN,
    LOCAL_PROTOCOLS,
)
from .local_api import LocalApiError, MhLocalApiClient

_LOGGER = logging.getLogger(__package__)


class MhFlowHandler(ConfigFlow, domain=DOMAIN):
    """Config flow for myheat."""

    VERSION = 2
    CONNECTION_CLASS = CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        super().__init__()
        self._auth: dict[str, Any] = {}
        self._local: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: cloud auth + local-mode toggles.

        Cloud creds are validated HERE, before we move on. If the user picked
        "local only" we skip cloud validation entirely. Otherwise empty or
        wrong creds → error stays on this screen with the checkboxes/inputs
        preserved (instead of confusingly surfacing on the local step).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            local_only = bool(user_input.get(CONF_LOCAL_ONLY))
            local_enabled = local_only or bool(user_input.get(CONF_LOCAL_ENABLED))

            self._local = {
                CONF_LOCAL_ENABLED: local_enabled,
                CONF_LOCAL_ONLY: local_only,
            }

            if local_only:
                # No cloud at all — skip cloud validation, go straight to local.
                self._auth = {CONF_USERNAME: "", CONF_API_KEY: ""}
                return await self.async_step_local()

            # Cloud is in use (cloud-only or hybrid) — validate cloud right now.
            username = (user_input.get(CONF_USERNAME) or "").strip()
            api_key = (user_input.get(CONF_API_KEY) or "").strip()

            if not username or not api_key:
                errors["base"] = "invalid_auth"
            else:
                devices = await self._get_devices(
                    username=username, api_key=api_key
                )
                if not devices:
                    errors["base"] = "invalid_auth"
                else:
                    self._auth = {
                        CONF_USERNAME: username,
                        CONF_API_KEY: api_key,
                    }
                    self._devices = devices
                    if local_enabled:
                        return await self.async_step_local()
                    return await self.async_step_device()

        return self._show_user_form(user_input, errors)

    def _show_user_form(
        self,
        user_input: dict[str, Any] | None,
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
                ): str,
                vol.Optional(
                    CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")
                ): str,
                vol.Optional(
                    CONF_LOCAL_ENABLED,
                    default=defaults.get(CONF_LOCAL_ENABLED, False),
                ): bool,
                vol.Optional(
                    CONF_LOCAL_ONLY, default=defaults.get(CONF_LOCAL_ONLY, False)
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (when local mode enabled): host + local creds, validate connectivity."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._local.update(
                {
                    CONF_LOCAL_HOST: user_input[CONF_LOCAL_HOST].strip(),
                    CONF_LOCAL_LOGIN: user_input[CONF_LOCAL_LOGIN],
                    CONF_LOCAL_PASSWORD: user_input[CONF_LOCAL_PASSWORD],
                    CONF_LOCAL_PROTOCOL: user_input.get(
                        CONF_LOCAL_PROTOCOL, DEFAULT_LOCAL_PROTOCOL
                    ),
                    CONF_LOCAL_POLL_INTERVAL: int(
                        user_input.get(
                            CONF_LOCAL_POLL_INTERVAL, DEFAULT_LOCAL_POLL_INTERVAL
                        )
                    ),
                    CONF_LOCAL_TIMEOUT: int(
                        user_input.get(CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT)
                    ),
                }
            )

            ok = await self._probe_local(
                host=self._local[CONF_LOCAL_HOST],
                login=self._local[CONF_LOCAL_LOGIN],
                password=self._local[CONF_LOCAL_PASSWORD],
                protocol=self._local[CONF_LOCAL_PROTOCOL],
                timeout=self._local[CONF_LOCAL_TIMEOUT],
            )
            if not ok:
                errors["base"] = "cannot_connect_local"
            else:
                if self._local[CONF_LOCAL_ONLY]:
                    return await self._finish_local_only()
                # Cloud was already validated on step 1 — devices are in
                # self._devices, just proceed to device-pick step.
                return await self.async_step_device()

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_HOST, default=DEFAULT_LOCAL_HOST): str,
                vol.Required(CONF_LOCAL_LOGIN, default=DEFAULT_LOCAL_LOGIN): str,
                vol.Required(CONF_LOCAL_PASSWORD, default=DEFAULT_LOCAL_PASSWORD): str,
                vol.Optional(
                    CONF_LOCAL_PROTOCOL, default=DEFAULT_LOCAL_PROTOCOL
                ): vol.In(LOCAL_PROTOCOLS),
                vol.Optional(
                    CONF_LOCAL_POLL_INTERVAL, default=DEFAULT_LOCAL_POLL_INTERVAL
                ): vol.All(int, vol.Range(min=15, max=600)),
                vol.Optional(
                    CONF_LOCAL_TIMEOUT, default=DEFAULT_LOCAL_TIMEOUT
                ): vol.All(int, vol.Range(min=10, max=120)),
            }
        )
        return self.async_show_form(
            step_id="local", data_schema=schema, errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 3 (cloud or hybrid): pick a cloud device."""
        if not user_input:
            devices = [f"{v['id']} - {v['name']} - {v['city']}" for v in self._devices]
            return self.async_show_form(
                step_id="device",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_NAME): str,
                        vol.Required(CONF_DEVICE_ID): vol.In(devices),
                    }
                ),
            )

        device_id = int(user_input[CONF_DEVICE_ID].strip().split(" ")[0])
        unique_id = f"myheat{device_id}"

        data = {
            CONF_NAME: user_input[CONF_NAME],
            CONF_USERNAME: self._auth[CONF_USERNAME],
            CONF_API_KEY: self._auth[CONF_API_KEY],
            CONF_DEVICE_ID: device_id,
            CONF_DEVICE_KEY: str(device_id),
            **self._local,
        }
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=user_input[CONF_NAME], data=data)

    async def _finish_local_only(self) -> ConfigFlowResult:
        """Create entry without cloud account (local-only install).

        Fetches the controller serial via /api/getState so that the entry
        gets a stable CONF_DEVICE_KEY — entities will then survive a
        delete-and-recreate of the entry without renaming.
        """
        host = self._local[CONF_LOCAL_HOST]
        serial = await self._fetch_local_serial()
        device_key = f"local_{serial}" if serial else f"local_host_{host}"
        unique_id = f"myheat_{device_key}"
        data = {
            CONF_NAME: f"MyHeat ({host})",
            CONF_USERNAME: "",
            CONF_API_KEY: "",
            CONF_DEVICE_ID: 0,
            CONF_DEVICE_KEY: device_key,
            **self._local,
        }
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_NAME], data=data)

    async def _fetch_local_serial(self) -> str | None:
        """Best-effort call to /api/getState to grab the controller serial."""
        try:
            session = async_create_clientsession(self.hass)
            client = MhLocalApiClient(
                host=self._local[CONF_LOCAL_HOST],
                login=self._local[CONF_LOCAL_LOGIN],
                password=self._local[CONF_LOCAL_PASSWORD],
                session=session,
                protocol=self._local.get(CONF_LOCAL_PROTOCOL, DEFAULT_LOCAL_PROTOCOL),
                timeout=int(
                    self._local.get(CONF_LOCAL_TIMEOUT, DEFAULT_LOCAL_TIMEOUT)
                ),
            )
            state = await client.async_get_state()
            serial = state.get("serial")
            return str(serial) if serial else None
        except (LocalApiError, ValueError, Exception):  # noqa: BLE001
            _LOGGER.exception("failed to fetch local serial; falling back to host-based key")
            return None

    async def _get_devices(self, username: str, api_key: str) -> list[Any]:
        """Validate cloud creds by fetching device list."""
        try:
            session = async_create_clientsession(self.hass)
            client = MhApiClient(
                username=username,
                api_key=api_key,
                device_id=None,
                session=session,
            )
            result = await client.async_get_devices()
            return result["devices"]
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("failed to get devices during config flow")
            return []

    async def _probe_local(
        self,
        *,
        host: str,
        login: str,
        password: str,
        protocol: str = DEFAULT_LOCAL_PROTOCOL,
        timeout: int = DEFAULT_LOCAL_TIMEOUT,
    ) -> bool:
        """Verify the controller is reachable and credentials are accepted."""
        try:
            session = async_create_clientsession(self.hass)
            client = MhLocalApiClient(
                host=host,
                login=login,
                password=password,
                session=session,
                protocol=protocol,
                timeout=timeout,
            )
            await client.async_login()
            return True
        except (LocalApiError, ValueError):
            _LOGGER.exception("local controller probe failed")
            return False
