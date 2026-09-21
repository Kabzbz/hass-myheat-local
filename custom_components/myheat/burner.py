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
from homeassistant.const import UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GAS_RATE,
    CONF_GAS_RATE_MAX,
    CONF_GAS_RATE_MIN,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
    SOURCE_CLOUD,
    VERSION,
)
from .local_api import (  # noqa: F401 — flag constants re-exported for tests
    HEATER_FLAG_CH,
    HEATER_FLAG_DHW,
    HEATER_FLAG_FAULT,
    HEATER_FLAG_FLAME,
    HEATER_FLAG_LINK,
    HEATER_FLAG_LOW_PRESSURE,
    HEATER_FLAG_PUMP,
    LocalApiError,
    MhLocalApiClient,
)

_LOGGER = logging.getLogger(__package__)

# Integrate on-time only between samples that are at most this many poll
# intervals apart; a longer gap (controller not answering) is not counted.
MAX_GAP_INTERVALS = 3


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def gas_config(data) -> tuple[float, float | None, float | None]:
    """Gas settings from entry.data: (average m³/h, m³/h at 0 %, at 100 %).

    The range counts only when both ends are set and max >= min.
    """
    rate = _positive(data.get(CONF_GAS_RATE)) or 0.0
    low = _positive(data.get(CONF_GAS_RATE_MIN))
    high = _positive(data.get(CONF_GAS_RATE_MAX))
    if low is None or high is None or high < low:
        low = high = None
    return rate, low, high


def gas_configured(data) -> bool:
    rate, low, _high = gas_config(data)
    return rate > 0 or low is not None


def decode_heater_flags(flags: int) -> dict[str, bool]:
    return {
        "link": bool(flags & HEATER_FLAG_LINK),
        "fault": bool(flags & HEATER_FLAG_FAULT),
        "flame": bool(flags & HEATER_FLAG_FLAME),
        "pump": bool(flags & HEATER_FLAG_PUMP),
        "ch": bool(flags & HEATER_FLAG_CH),
        "dhw": bool(flags & HEATER_FLAG_DHW),
        "low_pressure": bool(flags & HEATER_FLAG_LOW_PRESSURE),
    }


class MhBurnerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /api/getObjState often and reports burner state + per-poll deltas.

    Each update carries the on-time, gas and ignitions that happened since
    the previous sample; the counter sensors add those deltas to their
    restored totals, so the totals survive HA restarts without shared storage.

    Gas: with the boiler's rate at 0 % and 100 % modulation set, each step
    burns rate = min + (max − min) × modulation / 100. Modulation comes from
    the cloud only (main coordinator); when it is unknown the average rate is
    used, or the middle of the range if no average rate is set.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry,
        local_client: MhLocalApiClient,
        interval_seconds: int,
        main=None,
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
        # main (cloud/local) coordinator: the only source of modulation
        self._main = main
        self._gas_rate, self._gas_min, self._gas_max = gas_config(entry.data)
        self._prev_t: float | None = None
        # previous (flame, flame-for-heating, flame-for-hot-water); None = unknown
        self._prev: tuple[bool, bool, bool] | None = None
        self._prev_state: dict[str, bool] | None = None
        self._seq = 0
        # pump overrun: flame out while the pump keeps running
        self._overrun_start: float | None = None
        self._overrun_after = ""
        self._overrun_seq = 0
        self._last_overrun: dict[str, Any] | None = None
        # last full /api/getObjState answer, shared with the main coordinator
        # so the controller is not asked for the same data twice
        self.last_obj: dict[str, Any] | None = None
        self.last_obj_at: float = 0.0

    def fresh_obj_state(self) -> dict[str, Any] | None:
        """The last getObjState answer if it is at most two poll steps old."""
        if self.last_obj is None:
            return None
        if time.monotonic() - self.last_obj_at > self._interval * 2:
            return None
        return self.last_obj

    @property
    def interval_seconds(self) -> int:
        return self._interval

    def _track_overrun(self, state: dict[str, bool], now: float, step_ok: bool) -> None:
        """Measure flame-out -> pump-stop, each edge at the midpoint of its poll step."""
        prev = self._prev_state
        if not step_ok or prev is None or self._prev_t is None:
            self._overrun_start = None  # gap or first sample: unknown
            return
        mid = (self._prev_t + now) / 2
        if self._overrun_start is not None:
            if state["flame"]:
                self._overrun_start = None  # re-ignited: not an overrun
            elif not state["pump"]:
                self._record_overrun(mid - self._overrun_start)
                self._overrun_start = None
            return
        if prev["flame"] and not state["flame"]:
            if prev["dhw"]:
                self._overrun_after = "ГВС"
            elif prev["ch"]:
                self._overrun_after = "отопление"
            else:
                self._overrun_after = ""
            if state["pump"]:
                self._overrun_start = mid
            else:
                # pump stopped within the same step: shorter than the interval
                self._record_overrun(0.0)

    def _cloud_modulation(self, heater_id: Any) -> float | None:
        """Burner modulation, %, from the cloud (the local API has none).

        None when unknown: the cloud is not the active source, its data is
        stale, or its snapshot (the controller sends one a minute) predates
        this burn — its 0 % would then mean "not burning", not "minimum".
        """
        main = self._main
        if main is None or main.active_source != SOURCE_CLOUD:
            return None
        data = main.data or {}
        if not data.get("dataActual"):
            return None
        heaters = data.get("heaters") or []
        heater = next((h for h in heaters if h.get("id") == heater_id), None)
        if heater is None:
            heater = heaters[0] if heaters else {}
        if not (heater.get("burnerHeating") or heater.get("burnerWater")):
            return None
        try:
            modulation = float(heater["modulation"])
        except (KeyError, TypeError, ValueError):
            return None
        return min(max(modulation, 0.0), 100.0)

    def _gas_rate_now(self, heater_id: Any) -> tuple[float, float | None]:
        """(m³/h for this step, modulation % it came from or None)."""
        if self._gas_min is None or self._gas_max is None:
            return self._gas_rate, None
        modulation = self._cloud_modulation(heater_id)
        if modulation is None:
            if self._gas_rate > 0:
                return self._gas_rate, None
            return (self._gas_min + self._gas_max) / 2, None
        span = self._gas_max - self._gas_min
        return self._gas_min + span * modulation / 100, modulation

    def _record_overrun(self, seconds: float) -> None:
        self._overrun_seq += 1
        self._last_overrun = {
            "id": self._overrun_seq,
            "seconds": round(max(seconds, 0.0), 1),
            "after": self._overrun_after,
            "ended": dt_util.now().isoformat(timespec="seconds"),
        }

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            obj = await self._local.async_get_obj_state()
        except LocalApiError as err:
            # Don't integrate across the gap; keep _prev to still detect an
            # ignition that happened while the controller was silent.
            self._prev_t = None
            raise UpdateFailed(f"burner poll: {err}") from err

        now = time.monotonic()
        self.last_obj, self.last_obj_at = obj, now
        heaters = obj.get("heaters") or []
        heater = heaters[0] if heaters else {}
        flags = int(heater.get("f") or 0)
        state = decode_heater_flags(flags)
        cur = (
            state["flame"],
            state["flame"] and state["ch"],
            state["flame"] and state["dhw"],
        )

        seconds = [0.0, 0.0, 0.0]
        step_ok = (
            self._prev_t is not None
            and self._prev is not None
            and now - self._prev_t <= self._interval * MAX_GAP_INTERVALS
        )
        if step_ok:
            dt = now - self._prev_t
            # Trapezoid: each end that was "on" contributes half the step,
            # so a transition costs at most ±interval/2 of error.
            seconds = [dt * (int(p) + int(c)) / 2 for p, c in zip(self._prev, cur)]
        # rising edges; nothing is counted on the very first sample
        started = [int(self._prev is not None and c and not p)
                   for p, c in zip(self._prev or (False,) * 3, cur)]
        self._track_overrun(state, now, step_ok)

        gas = [0.0, 0.0, 0.0]
        gas_rate = modulation = None
        if seconds[0] > 0:
            gas_rate, modulation = self._gas_rate_now(heater.get("i"))
            gas = [s * gas_rate / 3600 for s in seconds]

        self._prev_t, self._prev, self._prev_state = now, cur, state
        self._seq += 1
        return {
            "flags": flags,
            **state,
            "seq": self._seq,
            "on_seconds": seconds[0],
            "ch_seconds": seconds[1],
            "dhw_seconds": seconds[2],
            # m³ burned during this step (0 when gas is not configured)
            "gas_m3": gas[0],
            "gas_ch_m3": gas[1],
            "gas_dhw_m3": gas[2],
            "gas_rate": gas_rate or None,
            "gas_modulation": modulation,
            "ignitions": started[0],
            "dhw_starts": started[2],
            "last_overrun": self._last_overrun,
        }


def _burner_entities(coordinator, entry, device_key: str, classes) -> list:
    burner = getattr(coordinator, "burner", None)
    heaters = (coordinator.data or {}).get("heaters") or []
    if burner is None or not heaters:
        return []
    return [cls(burner, entry, heaters[0], device_key) for cls in classes]


def burner_counter_sensors(coordinator, entry, device_key: str) -> list:
    """Sensor-platform entities of the fast local poller (local API only)."""
    classes = [
        MhBurnerRuntimeSensor,
        MhBurnerChRuntimeSensor,
        MhBurnerDhwRuntimeSensor,
        MhBurnerIgnitionsSensor,
        MhDhwStartsSensor,
        MhPumpOverrunSensor,
    ]
    if gas_configured(entry.data):
        classes += [MhGasTotalSensor, MhGasChSensor, MhGasDhwSensor]
    return _burner_entities(coordinator, entry, device_key, classes)


def burner_binary_sensors(coordinator, entry, device_key: str) -> list:
    """Binary-sensor-platform entities of the fast local poller."""
    return _burner_entities(
        coordinator,
        entry,
        device_key,
        (
            MhPumpBinarySensor,
            MhBoilerFaultBinarySensor,
            MhLowPressureBinarySensor,
            MhBoilerLinkBinarySensor,
        ),
    )


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
        self._entry = entry
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
    # a unit makes HA keep long-term statistics (utility_meter, graphs)
    _attr_native_unit_of_measurement = "раз"
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


class MhPumpBinarySensor(_MhBurnerEntity, BinarySensorEntity):
    """Boiler pump running now: burning, overrun after the flame, or circulating."""

    _key = "local_pump"
    _label = "Насос"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:pump"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else bool(data.get("pump"))


class MhPumpOverrunSensor(_MhBurnerEntity, RestoreSensor):
    """How long the pump kept running after the flame went out, last time."""

    _key = "pump_overrun"
    _label = "Выбег насоса"
    _attr_icon = "mdi:pump"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._value: float | None = None
        self._after = ""
        self._ended: str | None = None
        self._last_id: int | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._value = float(last.native_value)
            except (TypeError, ValueError):
                self._value = None
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._after = last_state.attributes.get("после", "")
            self._ended = last_state.attributes.get("закончился")

    @callback
    def _handle_coordinator_update(self) -> None:
        rec = (self.coordinator.data or {}).get("last_overrun")
        if rec and rec["id"] != self._last_id:
            self._last_id = rec["id"]
            self._value = rec["seconds"]
            self._after = rec["after"]
            self._ended = rec["ended"]
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        # the last measured overrun stays valid while the controller is silent
        return self._value is not None or self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "после": self._after,
            "закончился": self._ended,
            "точность": f"±{self.coordinator.interval_seconds} с",
        }


class _MhFlagBinarySensor(_MhBurnerEntity, BinarySensorEntity):
    """A heater flag bit from the fast local poller."""

    _flag_key = ""

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return None if data is None else bool(data.get(self._flag_key))


class MhBoilerFaultBinarySensor(_MhFlagBinarySensor):
    """Boiler reports an error (web UI: "Ошибка на котле")."""

    _key = "local_fault"
    _label = "Ошибка котла"
    _flag_key = "fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-circle"


class MhLowPressureBinarySensor(_MhFlagBinarySensor):
    """Web UI: "Низкое давление в контуре отопления"."""

    _key = "local_low_pressure"
    _label = "Низкое давление"
    _flag_key = "low_pressure"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:gauge-low"


class MhBoilerLinkBinarySensor(_MhFlagBinarySensor):
    """Controller <-> boiler link (web UI shows "Нет связи с котлом" when off)."""

    _key = "local_link"
    _label = "Связь с котлом"
    _flag_key = "link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY


class _MhGasSensor(MhBurnerRuntimeSensor):
    """Gas estimate for the Energy dashboard, m³.

    The burner poller works out the m³ of every step (from the modulation or
    the configured rate); changed settings apply from then on, the
    accumulated total is kept.
    """

    _attr_icon = "mdi:fire"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_suggested_display_precision = 3
    _scale = 1.0


class MhGasTotalSensor(_MhGasSensor):
    _key = "gas_total"
    _label = "Газ (оценка)"
    _delta_key = "gas_m3"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data or {}
        rate = data.get("gas_rate")
        modulation = data.get("gas_modulation")
        if modulation is not None and float(modulation).is_integer():
            modulation = int(modulation)
        return {
            "расход_м3_ч": round(rate, 3) if rate else None,
            "модуляция": modulation,
        }


class MhGasChSensor(_MhGasSensor):
    _key = "gas_ch"
    _label = "Газ на отопление (оценка)"
    _delta_key = "gas_ch_m3"


class MhGasDhwSensor(_MhGasSensor):
    _key = "gas_dhw"
    _label = "Газ на ГВС (оценка)"
    _delta_key = "gas_dhw_m3"
