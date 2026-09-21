"""Fast local burner tracking.

The controller pushes boiler state to the cloud only once a minute, so burner
run time taken from the cloud is off by up to a minute per ignition. The local
controller knows the state in real time; polling it every few seconds gives
±interval/2 per transition instead.

Heater "f" bits (constants and evidence live in local_api.py):
    0x0001 flame                 0x0002 boiler fault
    0x0004 pump                  0x0008 working for heating
    0x0010 working for hot water 0x0020 link with boiler OK
Also shown by the controller's web UI: 0x0040 warning on display,
0x0080 maintenance, 0x0200 no response to heat request, 0x0400 low pressure,
0x1000 raise max target temperature.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import CONF_NAME, DEFAULT_NAME, DOMAIN, MANUFACTURER, VERSION
from .local_api import (  # noqa: F401 — flag constants re-exported for tests
    HEATER_FLAG_CH,
    HEATER_FLAG_DHW,
    HEATER_FLAG_FAULT,
    HEATER_FLAG_FLAME,
    HEATER_FLAG_LINK,
    HEATER_FLAG_PUMP,
    LocalApiError,
    MhLocalApiClient,
)

_LOGGER = logging.getLogger(__package__)

# Integrate on-time only between samples that are at most this many poll
# intervals apart; a longer gap (controller not answering) is not counted.
MAX_GAP_INTERVALS = 3


def decode_heater_flags(flags: int) -> dict[str, bool]:
    return {
        "link": bool(flags & HEATER_FLAG_LINK),
        "fault": bool(flags & HEATER_FLAG_FAULT),
        "flame": bool(flags & HEATER_FLAG_FLAME),
        "pump": bool(flags & HEATER_FLAG_PUMP),
        "ch": bool(flags & HEATER_FLAG_CH),
        "dhw": bool(flags & HEATER_FLAG_DHW),
    }


class MhBurnerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /api/getObjState often and reports burner state + per-poll deltas.

    Each update carries the on-time and ignitions that happened since the
    previous sample; the counter sensors add those deltas to their restored
    totals, so the totals survive HA restarts without shared storage.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        local_client: MhLocalApiClient,
        interval_seconds: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} burner",
            update_interval=timedelta(seconds=interval_seconds),
            always_update=True,
        )
        self._local = local_client
        self._interval = interval_seconds
        self._prev_t: float | None = None
        self._prev_on: bool | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            obj = await self._local.async_get_obj_state()
        except LocalApiError as err:
            # Don't integrate across the gap; keep _prev_on to still detect
            # an ignition that happened while the controller was silent.
            self._prev_t = None
            raise UpdateFailed(f"burner poll: {err}") from err

        now = time.monotonic()
        heaters = obj.get("heaters") or []
        flags = int((heaters[0] if heaters else {}).get("f") or 0)
        state = decode_heater_flags(flags)
        on = state["flame"]

        on_seconds = 0.0
        ignitions = 0
        if self._prev_t is not None:
            dt = now - self._prev_t
            if dt <= self._interval * MAX_GAP_INTERVALS:
                # Trapezoid: each end that was "on" contributes half the step,
                # so a transition costs at most ±interval/2 of error.
                on_seconds = dt * (int(bool(self._prev_on)) + int(on)) / 2
        if on and self._prev_on is False:
            ignitions = 1

        self._prev_t, self._prev_on = now, on
        return {
            "flags": flags,
            **state,
            "on_seconds": on_seconds,
            "ignitions": ignitions,
        }


def burner_entities_for(coordinator, entry, device_key: str, kind: str) -> list:
    """Entities of one platform ("binary_sensor" / "sensor") for the first heater."""
    burner = getattr(coordinator, "burner", None)
    heaters = (coordinator.data or {}).get("heaters") or []
    if burner is None or not heaters:
        return []
    heater = heaters[0]
    if kind == "binary_sensor":
        return [MhFlameBinarySensor(burner, entry, heater, device_key)]
    return [
        MhBurnerRuntimeSensor(burner, entry, heater, device_key),
        MhBurnerIgnitionsSensor(burner, entry, heater, device_key),
    ]


class _MhBurnerEntity(CoordinatorEntity[MhBurnerCoordinator]):
    """Attached to the boiler device.

    Full DeviceInfo (not identifiers-only): if this entity happens to be the
    first one to register the boiler device, an identifiers-only link would
    create it under the config entry title and mangle names/entity_ids.
    """

    _key = ""
    _label = ""

    def __init__(self, coordinator, entry, heater: dict, device_key: str) -> None:
        super().__init__(coordinator)
        base = entry.data.get(CONF_NAME, DEFAULT_NAME)
        self._attr_name = f"{base} {heater['name']} {self._label}"
        self._attr_unique_id = f"{device_key}htr{heater['id']}{self._key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{device_key}htr{heater['id']}")},
            name=f"{base} {heater['name']}",
            manufacturer=MANUFACTURER,
            model=VERSION,
        )


class MhFlameBinarySensor(_MhBurnerEntity, BinarySensorEntity):
    """Burner flame, read from the controller every few seconds."""

    _key = "local_flame"
    _label = "Пламя"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:fire"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else bool(data.get("flame"))

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        return {
            "на_отопление": data.get("ch"),
            "на_гвс": data.get("dhw"),
            "насос": data.get("pump"),
            "связь_с_котлом": data.get("link"),
            "ошибка_котла": data.get("fault"),
            "флаги": data.get("flags"),
        }


class _MhBurnerCounter(_MhBurnerEntity, RestoreSensor):
    """Accumulates per-poll deltas on top of the value restored after restart."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _delta_key = ""
    _scale = 1.0

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._total = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._total = float(last.native_value)
            except (TypeError, ValueError):
                self._total = 0.0

    @callback
    def _handle_coordinator_update(self) -> None:
        data = self.coordinator.data or {}
        self._total += float(data.get(self._delta_key) or 0) * self._scale
        self.async_write_ha_state()


class MhBurnerRuntimeSensor(_MhBurnerCounter):
    _key = "burner_runtime"
    _label = "Время работы горелки"
    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 2
    _delta_key = "on_seconds"
    _scale = 1 / 3600

    @property
    def native_value(self) -> float:
        return round(self._total, 5)


class MhBurnerIgnitionsSensor(_MhBurnerCounter):
    _key = "burner_ignitions"
    _label = "Розжиги горелки"
    _attr_icon = "mdi:counter"
    _delta_key = "ignitions"

    @property
    def native_value(self) -> int:
        return int(self._total)
