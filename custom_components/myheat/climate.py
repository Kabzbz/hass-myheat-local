"""Climate platform for MyHeat."""

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import (
    ENV_TYPE_BOILER_TEMPERATURE,
    ENV_TYPE_DHW_TEMPERATURE,
    ENV_TYPE_HUMIDITY,
)
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .entity import MhEnvEntity

WATER_HEATER_ENV_TYPES = (ENV_TYPE_BOILER_TEMPERATURE, ENV_TYPE_DHW_TEMPERATURE)

# Cloud-only installs have no way to read the controller's mode list, so they
# keep the historical fixed mapping. Mode ids are user-configurable on the
# controller, so with the local API enabled the real list is used instead.
PRESET_TO_ID = {
    PRESET_AWAY: 3,
    PRESET_ECO: 2,
    PRESET_HOME: 1,
    PRESET_NONE: 0,
    PRESET_SLEEP: 4,
}

SCHEDULE_PREFIX = "📅 "


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MhConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Setup climate platform."""
    coordinator: MhDataUpdateCoordinator = entry.runtime_data

    async_add_entities(
        [
            MhEnvClimate(coordinator, entry, env)
            for env in coordinator.data.get("envs", [])
            if env.get("type") not in WATER_HEATER_ENV_TYPES
            and env.get("type") != ENV_TYPE_HUMIDITY
            and not env.get("_readonly")
        ]
    )


def _temp_limits(env: dict) -> tuple[float, float]:
    """Min/max target temperature by environment type (and name as a hint)."""
    env_type = env.get("type", "")
    env_name = (env.get("name") or "").lower()
    if "бойлер" in env_name or "гвс" in env_name or env_type == "boiler_temperature":
        return 20, 60
    if "пол" in env_name or env_type == "floor_temperature":
        return 15, 45
    if "контур" in env_name or "отоплен" in env_name or env_type == "circuit_temperature":
        return 20, 85
    if "помещен" in env_name or "комнат" in env_name or env_type == "room_temperature":
        return 5, 30
    return 5, 85


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class MhEnvClimate(MhEnvEntity, ClimateEntity):
    """myheat Climate class."""

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_target_temperature_step = 1.0
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        env: dict,
    ):
        super().__init__(coordinator, config_entry, env)
        self._attr_min_temp, self._attr_max_temp = _temp_limits(env)
        self._attr_preset_mode = PRESET_NONE
        self._attr_preset_modes = [PRESET_NONE]
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_hvac_action = None
        self._attr_current_temperature = None
        self._attr_target_temperature = None
        # Fill state right away: otherwise after an HA restart the thermostat
        # shows "off" with no temperature until the next poll.
        self._update_from_data()

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        e = self.get_env()
        return {
            "env_type": e.get("type", ""),
            "env_id": self.env_id,
            "demand": e.get("demand", False),
            "severity": e.get("severity", 1),
            "severity_desc": e.get("severityDesc", ""),
        }

    # --- presets = the controller's heating modes and schedules ---

    def _controller_presets(self) -> dict[str, tuple[str, int]] | None:
        """{preset name: ("mode"|"schedule", id)}, or None if not known."""
        local = (self.coordinator.data or {}).get("_local") or {}
        modes = local.get("hModes") or []
        schedules = local.get("scheds") or []
        if not modes and not schedules:
            return None
        table: dict[str, tuple[str, int]] = {}
        for m in modes:
            name, obj_id = (m.get("n") or "").strip(), _positive_int(m.get("i"))
            if name and obj_id:
                table[name] = ("mode", obj_id)
        for s in schedules:
            name, obj_id = (s.get("n") or "").strip(), _positive_int(s.get("i"))
            if name and obj_id:
                table[SCHEDULE_PREFIX + name] = ("schedule", obj_id)
        return table

    def _update_presets(self) -> None:
        table = self._controller_presets()
        if table is not None:
            local = (self.coordinator.data or {}).get("_local") or {}
            h_mode = _positive_int(local.get("hMode"))
            sched = _positive_int(local.get("sched"))
            current = PRESET_NONE
            # Same precedence as the controller's web UI: a manual mode wins.
            for name, (kind, obj_id) in table.items():
                if kind == "mode" and h_mode == obj_id:
                    current = name
                    break
            else:
                for name, (kind, obj_id) in table.items():
                    if kind == "schedule" and h_mode is None and sched == obj_id:
                        current = name
                        break
            self._attr_preset_modes = [PRESET_NONE, *table]
            self._attr_preset_mode = current
        elif self.coordinator.local_enabled:
            # Controller list not loaded yet: never offer the fixed mapping
            # here — its ids may point at a different mode on this controller.
            self._attr_preset_modes = [PRESET_NONE]
            self._attr_preset_mode = None
        else:
            self._attr_preset_modes = list(PRESET_TO_ID)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Switch the controller's heating mode / schedule."""
        table = self._controller_presets()
        api = self.coordinator.api
        if preset_mode == PRESET_NONE:
            await api.async_set_heating_mode(mode_id=0)
        elif table is not None:
            target = table.get(preset_mode)
            if target is None:
                raise ServiceValidationError(f"Неизвестный режим: {preset_mode}")
            kind, obj_id = target
            if kind == "mode":
                await api.async_set_heating_mode(mode_id=obj_id)
            else:
                await api.async_set_heating_mode(schedule_id=obj_id)
        elif not self.coordinator.local_enabled and preset_mode in PRESET_TO_ID:
            await api.async_set_heating_mode(mode_id=PRESET_TO_ID[preset_mode])
        else:
            raise ServiceValidationError(
                "Список режимов контроллера ещё не загружен, попробуйте позже"
            )
        self._attr_preset_mode = preset_mode
        self.async_write_ha_state()
        # The mode list/current mode comes from the local API; re-read it now
        # instead of waiting for the 10-minute background refresh.
        self.coordinator.mark_local_stale()
        await self.coordinator.async_request_refresh()

    # --- temperature / hvac ---

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            goal = None
        else:
            goal = self._attr_target_temperature
            if goal is None:
                goal = 24  # some reasonable value to turn the heater on
        await self.coordinator.api.async_set_env_goal(obj_id=self.env_id, goal=goal)
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        goal = kwargs.get("temperature", 0.0)
        await self.coordinator.api.async_set_env_goal(obj_id=self.env_id, goal=goal)
        await self.coordinator.async_request_refresh()

    def _update_from_data(self) -> None:
        e = self.get_env()
        if e:
            self._attr_min_temp, self._attr_max_temp = _temp_limits(e)
            self._attr_current_temperature = e.get("value")
            self._attr_target_temperature = e.get("target")
            self._attr_hvac_action = (
                (HVACAction.HEATING if e.get("demand", False) else HVACAction.IDLE)
                if self._attr_target_temperature is not None
                else HVACAction.OFF
            )
            self._attr_hvac_mode = (
                HVACMode.HEAT
                if self._attr_target_temperature is not None
                else HVACMode.OFF
            )
        self._update_presets()

    @callback
    def _handle_coordinator_update(self):
        """Get the latest state from the thermostat."""
        self._update_from_data()
        self.async_write_ha_state()
