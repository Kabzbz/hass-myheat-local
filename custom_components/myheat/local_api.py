"""Local HTTP API client for MyHeat controllers.

The controller exposes a tiny JSON RPC over HTTP (port 80, gzip+chunked):
    POST /api/login        {"login":"...","pswd":"..."}  -> {"status": true}, sets EspSessId cookie
    POST /api/getState     {}  -> connectivity / SIM state
    POST /api/getObjState  {}  -> sensors, heaters, modes, schedules
    POST /api/getSensors   {}  -> raw 1-Wire/discrete sensors
    POST /api/setObjState  {...}  -> control commands

The device is slow (multi-second stalls) and occasionally drops sessions, so:
- generous timeout (default 30 s)
- single in-flight request (asyncio.Lock)
- transparent re-login on auth failure or login-page response

A translator is provided to map the local JSON shape to the cloud
``getDeviceInfo`` shape so that the existing entities can consume it
without modification.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
NO_VALUE_SENTINEL = -16777216

# Local env "type" t-codes seen in the wild — used to disambiguate
# inside the heating_circuit subtype:
LOCAL_T_ROOM = 101            # room temperature
LOCAL_T_HEATING_CIRCUIT = 102 # heating circuit (supply)
LOCAL_T_DHW_CIRCUIT = 103     # DHW circuit
LOCAL_T_PROBE = 112           # generic temperature probe (e.g. boiler-room sensor)

# Heater "f" flag bits, derived empirically by comparing snapshots:
# value went from f=2349 (boiler firing, supply 57.5°C) to f=2336 (boiler off, supply 27.5°C).
# Diff was bits 0/2/3. The safest single-bit check for "burner on" is bit 0.
HEATER_FLAG_BURNER_ON = 0x1


class LocalApiError(Exception):
    """Generic local-API failure (network/HTTP/parse)."""


class LocalAuthError(LocalApiError):
    """Login was rejected or session is unrecoverable."""


class LocalUnsupportedError(LocalApiError):
    """Operation is not supported by the local API."""

    def __init__(self) -> None:
        super().__init__("В локальном режиме не поддерживается")


class MhLocalApiClient:
    """Minimal async client for a single MyHeat controller's local web API."""

    def __init__(
        self,
        *,
        host: str,
        login: str,
        password: str,
        session: aiohttp.ClientSession,
        timeout: int = DEFAULT_TIMEOUT,
        protocol: str = "http",
    ) -> None:
        proto = (protocol or "http").lower()
        if proto not in ("http", "https"):
            raise ValueError(f"unsupported local protocol {protocol!r}")
        self._base = f"{proto}://{host.strip().rstrip('/')}"
        self._login = login
        self._password = password
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()
        self._authenticated = False

    @property
    def host(self) -> str:
        return self._base

    async def async_login(self) -> None:
        url = f"{self._base}/api/login"
        try:
            async with self._session.post(
                url,
                json={"login": self._login, "pswd": self._password},
                timeout=self._timeout,
            ) as resp:
                if resp.status != 200:
                    raise LocalApiError(f"login HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LocalApiError(f"login failed: {err}") from err

        if not isinstance(data, dict) or not data.get("status"):
            raise LocalAuthError("controller rejected credentials")
        self._authenticated = True
        _LOGGER.debug("MyHeat local login OK at %s", self._base)

    async def _post(self, path: str, payload: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        try:
            async with self._session.post(
                url, json=payload or {}, timeout=self._timeout
            ) as resp:
                if resp.status in (401, 403):
                    raise LocalAuthError(f"unauthorized HTTP {resp.status}")
                if resp.status != 200:
                    raise LocalApiError(f"{path} HTTP {resp.status}")
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "json" not in ctype:
                    text = await resp.text()
                    if "<html" in text.lower() or "login" in text.lower()[:200]:
                        raise LocalAuthError("got login page — session expired")
                    raise LocalApiError(f"{path}: unexpected content-type {ctype}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise LocalApiError(f"{path}: {err}") from err

    async def _post_with_relogin(self, path: str, payload: dict | None = None) -> Any:
        async with self._lock:
            if not self._authenticated:
                await self.async_login()
            try:
                return await self._post(path, payload)
            except LocalAuthError:
                _LOGGER.debug("Local session expired on %s — relogin", path)
                self._authenticated = False
                await self.async_login()
                return await self._post(path, payload)

    # --- read methods ---

    async def async_get_state(self) -> dict[str, Any]:
        return await self._post_with_relogin("/api/getState")

    async def async_get_obj_state(self) -> dict[str, Any]:
        return await self._post_with_relogin("/api/getObjState")

    async def async_get_sensors(self) -> list[dict[str, Any]]:
        return await self._post_with_relogin("/api/getSensors")

    # --- write methods ---

    async def async_set_env_goal(
        self, *, obj_id: int, goal: float | int | None
    ) -> None:
        # value=None means "reset/turn off" — local equivalent is sentinel.
        value = NO_VALUE_SENTINEL if goal is None else float(goal)
        await self._post_with_relogin(
            "/api/setObjState",
            {"id": int(obj_id), "target": "env", "value": value},
        )

    async def async_set_env_curve(self, *, obj_id: int, curve: int) -> None:
        await self._post_with_relogin(
            "/api/setObjState",
            {"id": int(obj_id), "target": "env", "curve": int(curve)},
        )

    async def async_set_eng_goal(
        self, *, obj_id: int, goal: float | int
    ) -> None:
        await self._post_with_relogin(
            "/api/setObjState",
            {"id": int(obj_id), "target": "eng", "value": float(goal)},
        )

    async def async_set_heater_goal(
        self, *, obj_id: int, goal: float | int
    ) -> None:
        await self._post_with_relogin(
            "/api/setObjState",
            {"id": int(obj_id), "target": "heater", "value": float(goal)},
        )

    async def async_set_heating_mode(
        self,
        *,
        mode_id: int | None = None,
        schedule_id: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"action": "setHeatingMode"}
        if mode_id is not None:
            payload["mode"] = int(mode_id)
        if schedule_id is not None:
            payload["schedule"] = int(schedule_id)
        await self._post_with_relogin("/api/setObjState", payload)

    async def async_set_security_mode(self, *, mode: bool) -> None:
        action = "armSecurity" if mode else "disarmSecurity"
        await self._post_with_relogin("/api/setObjState", {"action": action})


# --- translator: local JSON -> cloud-shaped getDeviceInfo dict ---

_LOCAL_TYPE_TO_CLOUD = {
    # (s.p3001, env.t) -> cloud env type string
    ("heating_circuit", LOCAL_T_ROOM): "room_temperature",
    ("heating_circuit", LOCAL_T_HEATING_CIRCUIT): "circuit_temperature",
    ("heating_circuit", LOCAL_T_PROBE): "boiler_temperature",
    ("dhw_circuit", LOCAL_T_DHW_CIRCUIT): "dhw_temperature",
    ("dhw_circuit", LOCAL_T_PROBE): "boiler_temperature",
}


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == NO_VALUE_SENTINEL:
        return None
    return f


def _env_type(env: dict) -> str:
    sub = (env.get("s") or {}).get("p3001") or ""
    t = env.get("t")
    return _LOCAL_TYPE_TO_CLOUD.get((sub, t), "temperature")


def translate_local_to_cloud(
    *,
    state: dict | None,
    obj_state: dict | None,
) -> dict[str, Any]:
    """Translate local /api/getState + /api/getObjState into cloud
    ``getDeviceInfo`` shape consumed by entity.py / sensor.py / climate.py.

    Missing-in-local fields are filled with reasonable defaults
    (modulation=0, weatherTemp=None, city='') so cloud-oriented
    entities don't crash.
    """
    state = state or {}
    obj = obj_state or {}

    # heaters
    heaters: list[dict[str, Any]] = []
    for h in obj.get("heaters", []) or []:
        st = h.get("st") or {}
        flags = int(h.get("f") or 0)
        flow = _safe_float(st.get("p100"))
        target = _safe_float(st.get("p101"))
        pressure = _safe_float(st.get("p109"))
        burner_on = bool(flags & HEATER_FLAG_BURNER_ON)
        heaters.append(
            {
                "id": h.get("i"),
                "name": h.get("n"),
                "disabled": False,
                "flowTemp": flow if flow is not None else 0.0,
                "returnTemp": flow if flow is not None else 0.0,
                "pressure": pressure,
                "targetTemp": target if target is not None else 0.0,
                "burnerHeating": burner_on,
                "burnerWater": False,
                "modulation": 0,  # not exposed locally
            }
        )

    # envs
    envs: list[dict[str, Any]] = []
    for e in obj.get("envs", []) or []:
        st = e.get("st") or {}
        envs.append(
            {
                "id": e.get("i"),
                "type": _env_type(e),
                "name": (e.get("n") or "").strip(),
                "value": _safe_float(st.get("p1")) or 0.0,
                "target": _safe_float(st.get("p4")),
                "demand": bool(int(e.get("f") or 0) & 0x80),
                "severity": int(e.get("sev") or 0),
                "severityDesc": "",
            }
        )

    # engines
    engs: list[dict[str, Any]] = []
    for g in obj.get("engs", []) or []:
        flags = int(g.get("f") or 0)
        engs.append(
            {
                "id": g.get("i"),
                "type": "engine",
                "name": (g.get("n") or "").strip(),
                "turnedOn": bool(flags & 0x1),
                "severity": int(g.get("sev") or 0),
                "severityDesc": "",
            }
        )

    severity = int(obj.get("deviceSeverity") or 0)
    inet_up = str(state.get("inet") or "0") == "1"

    return {
        "heaters": heaters,
        "envs": envs,
        "engs": engs,
        "alarms": obj.get("alarms") or [],
        "dataActual": True,
        "severity": severity,
        "severityDesc": "",
        "weatherTemp": None,
        "city": "",
        # Local extras — non-cloud fields used by local-aware sensors
        "_local": {
            "inet": inet_up,
            "wifiSsid": state.get("wifiSsid") or "",
            "gsmRssi": _safe_float(state.get("gsmRssi")),
            "gsmBalance": _safe_float(state.get("gsmBalance")),
            "gsmCarrier": state.get("gsmCarrier") or "",
            "serial": state.get("serial") or "",
            "sched": obj.get("sched"),
            "hMode": obj.get("hMode"),
            "hModes": obj.get("hModes") or [],
            "scheds": obj.get("scheds") or [],
            "deviceFlags": obj.get("deviceFlags"),
            "simSignal": obj.get("simSignal"),
            "simBalance": obj.get("simBalance"),
        },
    }
