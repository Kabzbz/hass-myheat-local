"""Sample API Client."""

import asyncio
import logging
import socket
from typing import Any, Union

import aiohttp
import voluptuous as vol

from .const import VERSION
from .local_api import LocalApiError, LocalUnsupportedError, MhLocalApiClient

TIMEOUT = 10

_LOGGER: logging.Logger = logging.getLogger(__package__)

ENV_TYPE_ROOM_TEMPERATURE = "room_temperature"
ENV_TYPE_CIRCUIT_TEMPERATURE = "circuit_temperature"
ENV_TYPE_BOILER_TEMPERATURE = "boiler_temperature"
ENV_TYPE_DHW_TEMPERATURE = "dhw_temperature"
ENV_TYPE_FLOOR_TEMPERATURE = "floor_temperature"  # added in upstream 0.8.0

# Env types that should be exposed as Climate entities (vs. Water Heater).
CLIMATE_ENV_TYPES = (
    ENV_TYPE_ROOM_TEMPERATURE,
    ENV_TYPE_CIRCUIT_TEMPERATURE,
    ENV_TYPE_FLOOR_TEMPERATURE,
    "temperature",
)

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": f"homeassistant-myheat/{VERSION}",
}

RPC_ENDPOINT = "https://my.myheat.net/api/request/"

RPC_SCHEMA = vol.Schema(
    {
        vol.Required("err"): int,
        vol.Optional("refreshPage"): bool,
        vol.Optional("data"): vol.Any(
            # getDevices
            vol.Schema(
                {
                    "devices": [
                        vol.Schema(
                            {
                                "id": int,
                                "name": str,
                                "severity": int,
                                "severityDesc": str,
                            },
                            extra=vol.ALLOW_EXTRA,
                        ),
                    ],
                },
                extra=vol.ALLOW_EXTRA,
            ),
            # getDeviceInfo
            vol.Schema(
                {
                    "heaters": [
                        vol.Schema(
                            {
                                "id": int,
                                "name": str,
                                "disabled": bool,
                                "flowTemp": float,
                                "returnTemp": float,
                                "pressure": vol.Any(None, float),
                                "targetTemp": float,
                                "burnerHeating": bool,
                                "burnerWater": bool,
                                "modulation": int,
                            },
                            extra=vol.ALLOW_EXTRA,
                        ),
                    ],
                    "envs": [
                        vol.Schema(
                            {
                                "id": int,
                                "type": str,
                                "name": str,
                                "value": float,
                                "target": vol.Any(None, float),
                                "demand": bool,
                                "severity": int,
                                "severityDesc": str,
                            },
                            extra=vol.ALLOW_EXTRA,
                        ),
                    ],
                    "engs": [
                        vol.Schema(
                            {
                                "id": int,
                                "type": str,
                                "name": str,
                                "turnedOn": bool,
                                "severity": int,
                                "severityDesc": str,
                            },
                            extra=vol.ALLOW_EXTRA,
                        ),
                    ],
                    "alarms": list,
                    "dataActual": bool,
                    "severity": int,
                    "severityDesc": str,
                    "weatherTemp": vol.Coerce(float),
                    "city": str,
                },
                extra=vol.ALLOW_EXTRA,
            ),
            # nothing more yet documented
        ),
    },
    extra=vol.ALLOW_EXTRA,
)


class RPCError(Exception):
    """API returned error"""

    code: int
    _full_response: dict[str, Any]

    def __init__(self, resp: dict[str, Any]):
        self.code = resp["err"]
        self._full_response = resp

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} code: {self.code}>"


class MhApiClient:
    """API helper to manipulate with myheat.net cloud, with optional local fallback.

    When a ``local_client`` is supplied, write operations transparently fall
    back to the controller's local HTTP API on cloud failure (or always, if
    ``local_only=True``). Operations that have no local equivalent raise
    ``LocalUnsupportedError`` with the message
    "В локальном режиме не поддерживается".
    """

    def __init__(
        self,
        *,
        username: str | None = None,
        api_key: str | None = None,
        device_id: int | None = None,
        session: aiohttp.ClientSession,
        local_client: "MhLocalApiClient | None" = None,
        local_only: bool = False,
    ) -> None:
        self._username = username
        self._api_key = api_key
        self._device_id: int | None = device_id
        self._session: aiohttp.ClientSession = session
        self._local = local_client
        self._local_only = bool(local_only)
        if local_only and local_client is None:
            raise ValueError("local_only=True requires a local_client")

    async def async_get_devices(self) -> dict:
        """Get available devices.

        With ``local_only`` there is exactly one device — synthesise the
        cloud-shaped response from the local serial number.
        """
        if self._local_only:
            assert self._local is not None
            try:
                state = await self._local.async_get_state()
            except LocalApiError as err:
                raise RPCError({"err": -1, "msg": str(err)}) from err
            return {
                "devices": [
                    {
                        "id": 1,
                        "name": "MyHeat (local)",
                        "city": "",
                        "severity": 0,
                        "severityDesc": "",
                        "serial": state.get("serial"),
                    }
                ]
            }
        return await self.rpc("getDevices")

    async def async_get_device_info(self, *, device_id: int | None = None) -> dict:
        """Get device state and objects (cloud, with optional local fallback)."""
        if self._local_only:
            return await self._fetch_via_local()
        try:
            return await self.rpc("getDeviceInfo", deviceId=device_id)
        except Exception as err:  # noqa: BLE001
            if self._local is None:
                raise
            _LOGGER.warning("cloud getDeviceInfo failed (%s) — using local", err)
            return await self._fetch_via_local()

    async def _fetch_via_local(self) -> dict:
        assert self._local is not None
        from .local_api import translate_local_to_cloud

        state = await self._local.async_get_state()
        obj = await self._local.async_get_obj_state()
        return translate_local_to_cloud(state=state, obj_state=obj)

    async def _route_write(self, cloud_call, local_call) -> None:
        """Route a write: cloud first, fall back to local on failure (or local-only)."""
        if self._local_only:
            if local_call is None:
                raise LocalUnsupportedError()
            await local_call()
            return
        try:
            await cloud_call()
        except Exception as cloud_err:  # noqa: BLE001
            if self._local is None or local_call is None:
                raise
            _LOGGER.warning(
                "cloud write failed (%s) — retrying via local", cloud_err
            )
            await local_call()

    async def async_set_env_goal(
        self,
        *,
        obj_id: int,
        goal: Union[int, float, None],
        device_id: int | None = None,
        change_mode: bool = False,
    ) -> None:
        """Set goal for environment."""
        await self._route_write(
            cloud_call=lambda: self.rpc(
                "setEnvGoal",
                deviceId=device_id,
                objId=obj_id,
                goal=goal,
                changeMode=change_mode and 1 or 0,
            ),
            local_call=(
                (lambda: self._local.async_set_env_goal(obj_id=obj_id, goal=goal))
                if self._local is not None
                else None
            ),
        )

    async def async_set_env_curve(
        self,
        *,
        obj_id: int,
        curve: int,
        device_id: int | None = None,
        change_mode: bool = False,
    ) -> None:
        """Set goal curve for environment."""
        await self._route_write(
            cloud_call=lambda: self.rpc(
                "setEnvCurve",
                deviceId=device_id,
                objId=obj_id,
                curve=curve,
                changeMode=change_mode and 1 or 0,
            ),
            local_call=(
                (lambda: self._local.async_set_env_curve(obj_id=obj_id, curve=curve))
                if self._local is not None
                else None
            ),
        )

    async def async_set_eng_goal(
        self,
        *,
        obj_id: int,
        goal: Union[int, float],
        device_id: int | None = None,
        change_mode: bool = False,
    ) -> None:
        """Set goal for engineering component."""
        await self._route_write(
            cloud_call=lambda: self.rpc(
                "setEngGoal",
                deviceId=device_id,
                objId=obj_id,
                goal=goal,
                changeMode=change_mode and 1 or 0,
            ),
            local_call=(
                (lambda: self._local.async_set_eng_goal(obj_id=obj_id, goal=goal))
                if self._local is not None
                else None
            ),
        )

    async def async_set_heating_mode(
        self,
        *,
        device_id: int | None = None,
        mode_id: int | None = None,
        schedule_id: int | None = None,
    ) -> None:
        """Set heating mode.

        Should be only one ID set: Mode or Schedule
        Value of 0 resets mode.
        """
        kvs: dict[str, Any] = {}
        if mode_id is not None:
            kvs["modeId"] = mode_id
        if schedule_id is not None:
            kvs["scheduleId"] = schedule_id

        await self._route_write(
            cloud_call=lambda: self.rpc("setHeatingMode", deviceId=device_id, **kvs),
            local_call=(
                (
                    lambda: self._local.async_set_heating_mode(
                        mode_id=mode_id, schedule_id=schedule_id
                    )
                )
                if self._local is not None
                else None
            ),
        )

    async def async_set_security_mode(
        self,
        *,
        mode: bool,
        device_id: int | None = None,
    ) -> None:
        """Set security alarm mode (on/off)."""
        await self._route_write(
            cloud_call=lambda: self.rpc(
                "setSecurityMode", deviceId=device_id, mode=mode and 1 or 0
            ),
            local_call=(
                (lambda: self._local.async_set_security_mode(mode=mode))
                if self._local is not None
                else None
            ),
        )

    async def rpc(self, action: str, **kwargs: dict) -> dict:
        """Get information from the API."""

        url = RPC_ENDPOINT

        kwargs["action"] = action
        kwargs["login"] = self._username
        kwargs["key"] = self._api_key

        # If deviceId is passed, and it is None => use the id stored in the instance
        if kwargs.get("deviceId", 1) is None:
            kwargs["deviceId"] = self._device_id

        try:
            async with asyncio.timeout(TIMEOUT):
                response = await self._session.post(url, headers=HEADERS, json=kwargs)
                data = await response.json()

                _LOGGER.debug("Data: %s", data)

                data = RPC_SCHEMA(data)
                if data["err"] != 0:
                    raise RPCError(data)

                return data.get("data", {})

        except (asyncio.TimeoutError, asyncio.CancelledError) as ex:
            _LOGGER.exception(
                "Timeout error fetching information from %s - %s",
                url,
                ex,
            )
            raise
        except (KeyError, TypeError) as ex:
            _LOGGER.exception(
                "Error parsing information from %s - %s",
                url,
                ex,
            )
            raise
        except (aiohttp.ClientError, socket.gaierror) as ex:
            _LOGGER.exception(
                "Error fetching information from %s - %s",
                url,
                ex,
            )
            raise
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.exception("Something really wrong happened! - %s", ex)
            raise

        raise AssertionError("reached unreachable code")
