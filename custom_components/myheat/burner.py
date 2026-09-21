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
        # previous (flame, flame-for-heating, flame-for-hot-water); None = unknown
        self._prev: tuple[bool, bool, bool] | None = None
        self._seq = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            obj = await self._local.async_get_obj_state()
        except LocalApiError as err:
            # Don't integrate across the gap; keep _prev to still detect an
            # ignition that happened while the controller was silent.
            self._prev_t = None
            raise UpdateFailed(f"burner poll: {err}") from err

        now = time.monotonic()
        heaters = obj.get("heaters") or []
        flags = int((heaters[0] if heaters else {}).get("f") or 0)
        state = decode_heater_flags(flags)
        cur = (
            state["flame"],
            state["flame"] and state["ch"],
            state["flame"] and state["dhw"],
        )

        seconds = [0.0, 0.0, 0.0]
        if self._prev_t is not None and self._prev is not None:
            dt = now - self._prev_t
            if dt <= self._interval * MAX_GAP_INTERVALS:
                # Trapezoid: each end that was "on" contributes half the step,
                # so a transition costs at most ±interval/2 of error.
                seconds = [dt * (int(p) + int(c)) / 2 for p, c in zip(self._prev, cur)]
        # rising edges; nothing is counted on the very first sample
        started = [int(self._prev is not None and c and not p)
                   for p, c in zip(self._prev or (False,) * 3, cur)]

        self._prev_t, self._prev = now, cur
        self._seq += 1
        return {
            "flags": flags,
            **state,
            "seq": self._seq,
            "on_seconds": seconds[0],
            "ch_seconds": seconds[1],
            "dhw_seconds": seconds[2],
            "ignitions": started[0],
            "dhw_starts": started[2],
        }


def burner_counter_sensors(coordinator, entry, device_key: str) -> list:
    """Run-time / ignition counters for the first heater (local API only)."""
    burner = getattr(coordinator, "burner", None)
    heaters = (coordinator.data or {}).get("heaters") or []
    if burner is None or not heaters:
        return []
    heater = heaters[0]
    return [
        cls(burner, entry, heater, device_key)
        for cls in (
            MhBurnerRuntimeSensor,
            MhBurnerChRuntimeSensor,
            MhBurnerDhwRuntimeSensor,
            MhBurnerIgnitionsSensor,
            MhDhwStartsSensor,
        )
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


class _MhBurnerCounter(_MhBurnerEntity, RestoreSensor):
    """Accumulates per-poll deltas on top of the value restored after restart."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _delta_key = ""
    _scale = 1.0

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._total = 0.0
        self._last_seq: int | None = None

    @property
    def available(self) -> bool:
        # The accumulated total is known even when the last poll failed; the
        # controller often misses a request, and "unavailable" gaps would
        # only clutter the history.
        return True

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
        seq = data.get("seq")
        # HA also notifies listeners when a poll fails, with the previous
        # (already counted) data — apply each sample exactly once.
        if seq is not None and seq != self._last_seq:
            self._last_seq = seq
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


class MhBurnerChRuntimeSensor(MhBurnerRuntimeSensor):
    _key = "burner_runtime_ch"
    _label = "Время работы на отопление"
    _attr_icon = "mdi:radiator"
    _delta_key = "ch_seconds"


class MhBurnerDhwRuntimeSensor(MhBurnerRuntimeSensor):
    _key = "burner_runtime_dhw"
    _label = "Время работы на ГВС"
    _attr_icon = "mdi:water-boiler"
    _delta_key = "dhw_seconds"


class MhBurnerIgnitionsSensor(_MhBurnerCounter):
    _key = "burner_ignitions"
    _label = "Розжиги горелки"
    _attr_icon = "mdi:counter"
    _delta_key = "ignitions"

    @property
    def native_value(self) -> int:
        return int(self._total)


class MhDhwStartsSensor(MhBurnerIgnitionsSensor):
    """Times the boiler started heating hot water (not necessarily a new
    ignition: a combi boiler can switch from heating to hot water with the
    flame kept on)."""

    _key = "dhw_starts"
    _label = "Включения ГВС"
    _attr_icon = "mdi:water-pump"
    _delta_key = "dhw_starts"
