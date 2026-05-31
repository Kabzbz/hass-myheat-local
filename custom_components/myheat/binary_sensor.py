"""Binary sensor platform for MyHeat."""

from itertools import chain

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SOURCE_CLOUD
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .entity import MhEngEntity, MhEntity, MhEnvEntity, MhHeaterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MhConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: MhDataUpdateCoordinator = entry.runtime_data

    extras: list = [MhCloudAvailableBinarySensor(coordinator, entry)]
    if coordinator.local_enabled:
        extras.append(MhLocalInternetBinarySensor(coordinator, entry))

    entities = chain(
        tuple(extras),
        (
            MhSeverityBinarySensor(coordinator, entry),
            MhDataActualBinarySensor(coordinator, entry),
            MhAlarmsBinarySensor(coordinator, entry),
        ),
        chain.from_iterable(
            [
                MhHeaterDisabledBinarySensor(coordinator, entry, heater),
                MhHeaterBurnerWaterBinarySensor(coordinator, entry, heater),
                MhHeaterBurnerHeatingBinarySensor(coordinator, entry, heater),
                MhHeaterBurnerBinarySensor(coordinator, entry, heater),  # Новый комбинированный сенсор
            ]
            for heater in coordinator.data.get("heaters", [])
        ),
        (
            MhEnvSeverityBinarySensor(coordinator, entry, env)
            for env in coordinator.data.get("envs", [])
        ),
        (
            MhEnvDemandBinarySensor(coordinator, entry, env)  # Сенсор запроса тепла для основных сред
            for env in coordinator.data.get("envs", [])
            if env.get("type") in ["room_temperature", "circuit_temperature", "boiler_temperature"]
        ),
        (
            MhEnvDemandBinarySensor(coordinator, entry, env)  # Сенсор запроса тепла для дополнительных сред
            for env in coordinator.data.get("envs", [])
            if env.get("type") not in ["room_temperature", "circuit_temperature", "boiler_temperature"]
            and env.get("demand") is not None
        ),
        chain.from_iterable(
            [
                MhEngTurnedOnBinarySensor(coordinator, entry, eng),
                MhEngSeverityBinarySensor(coordinator, entry, eng),
            ]
            for eng in coordinator.data.get("engs", [])
        ),
    )

    async_add_entities(entities)


class MhDataActualBinarySensor(MhEntity, BinarySensorEntity):
    """myheat Data Actual (connected) Binary Sensor class."""

    _attr_device_class = "connectivity"
    _attr_icon = "mdi:connection"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Подключение"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}data_actual"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("dataActual")

    @property
    def extra_state_attributes(self):
        return {
            "data_actual": self.coordinator.data.get("dataActual", False),
            "last_update": self.coordinator.last_update_success,
        }


class MhAlarmsBinarySensor(MhEntity, BinarySensorEntity):
    """myheat Alarms Binary Sensor class."""

    _attr_device_class = "safety"
    _attr_icon = "mdi:alarm-light"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Тревоги"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}alarms"

    @property
    def is_on(self) -> bool | None:
        alarms = self.coordinator.data.get("alarms", [])
        return len(alarms) > 0

    @property
    def extra_state_attributes(self):
        alarms = self.coordinator.data.get("alarms", [])
        return {
            "alarms": alarms,
            "alarms_count": len(alarms),
            "has_alarms": len(alarms) > 0,
        }


class MhSeverityBinarySensorBase(BinarySensorEntity):
    _attr_device_class = "problem"
    _attr_icon = "mdi:alert"

    def _severity(self) -> (int | None, str | None):
        return None, None

    @property
    def is_on(self) -> bool | None:
        severity, _ = self._severity()
        if severity is None:
            return None

        # device_class:problem -> on means problem detected
        return severity != 1

    @property
    def extra_state_attributes(self) -> dict:
        severity, desc = self._severity()
        return {
            "severity_code": severity,
            "severity_description": desc,
            "is_normal": severity == 1,
            "is_warning": severity == 32,
            "is_critical": severity == 64,
        }


class MhSeverityBinarySensor(MhEntity, MhSeverityBinarySensorBase):
    _attr_icon = "mdi:alert-circle"

    def _severity(self) -> (int | None, str | None):
        return (
            self.coordinator.data.get("severity"),
            self.coordinator.data.get("severityDesc"),
        )

    @property
    def name(self) -> str:
        return f"{self._mh_name} Статус системы"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}system_severity"


class MhEnvSeverityBinarySensor(MhEnvEntity, MhSeverityBinarySensorBase):
    _attr_icon = "mdi:thermometer-alert"

    def _severity(self) -> (int | None, str | None):
        e = self.get_env()
        return (
            e.get("severity"),
            e.get("severityDesc"),
        )

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.env_name} Статус"


class MhEnvDemandBinarySensor(MhEnvEntity, BinarySensorEntity):
    """Environment demand binary sensor"""
    
    _attr_device_class = "heat"
    _attr_icon = "mdi:fire"

    @property
    def name(self) -> str:
        env_type = self.get_env().get("type", "")
        type_map = {
            "room_temperature": "Помещение",
            "circuit_temperature": "Контур",
            "boiler_temperature": "Бойлер",
            "outdoor_temperature": "Уличный",
            "water_temperature": "Вода",
            "floor_temperature": "Теплый пол"
        }
        type_name = type_map.get(env_type, "Датчик")
        return f"{self._mh_name} {self.env_name} Запрос тепла ({type_name})"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}env{self.env_id}_demand"

    @property
    def is_on(self) -> bool | None:
        return self.get_env().get("demand", False)

    @property
    def extra_state_attributes(self):
        e = self.get_env()
        return {
            "env_type": e.get("type", ""),
            "current_temp": e.get("value"),
            "target_temp": e.get("target"),
            "severity": e.get("severity", 1),
            "severity_desc": e.get("severityDesc", ""),
        }


class MhEngSeverityBinarySensor(MhEngEntity, MhSeverityBinarySensorBase):
    _attr_icon = "mdi:alert-box"

    def _severity(self) -> (int | None, str | None):
        e = self.get_eng()
        return (
            e.get("severity"),
            e.get("severityDesc"),
        )

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.eng_name} Статус"


class MhHeaterBinarySensor(MhHeaterEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        return self.get_heater().get(self._key)

    @property
    def extra_state_attributes(self):
        heater = self.get_heater()
        return {
            "heater_id": self.heater_id,
            "flow_temp": heater.get("flowTemp"),
            "return_temp": heater.get("returnTemp"),
            "pressure": heater.get("pressure"),
            "modulation": heater.get("modulation"),
        }


class MhHeaterDisabledBinarySensor(MhHeaterBinarySensor):
    _key = "disabled"
    _attr_icon = "mdi:power-plug-off"
    _attr_device_class = None

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Отключен"


class MhHeaterBurnerWaterBinarySensor(MhHeaterBinarySensor):
    _key = "burnerWater"
    _attr_icon = "mdi:water-boiler"
    _attr_device_class = "heat"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} ГВС"


class MhHeaterBurnerHeatingBinarySensor(MhHeaterBinarySensor):
    _key = "burnerHeating"
    _attr_icon = "mdi:radiator"
    _attr_device_class = "heat"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Отопление"


class MhHeaterBurnerBinarySensor(MhHeaterEntity, BinarySensorEntity):
    """Combined burner state sensor"""
    
    _attr_device_class = "heat"
    _attr_icon = "mdi:fire"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Горелка"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}htr{self.heater_id}_burner_combined"

    @property
    def is_on(self) -> bool | None:
        heater = self.get_heater()
        return heater.get("burnerHeating", False) or heater.get("burnerWater", False)

    @property
    def extra_state_attributes(self) -> dict:
        heater = self.get_heater()
        return {
            "heating": heater.get("burnerHeating", False),
            "water": heater.get("burnerWater", False),
            "modulation": heater.get("modulation", 0),
            "flow_temp": heater.get("flowTemp"),
            "return_temp": heater.get("returnTemp"),
        }


class MhEngTurnedOnBinarySensor(MhEngEntity, BinarySensorEntity):
    _attr_icon = "mdi:power"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.eng_name} Состояние"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}eng{self.eng_id}_turned_on"

    @property
    def is_on(self) -> bool | None:
        return self.get_eng().get("turnedOn")

    @property
    def extra_state_attributes(self):
        eng = self.get_eng()
        return {
            "eng_type": eng.get("type", ""),
            "severity": eng.get("severity", 1),
            "severity_desc": eng.get("severityDesc", ""),
        }


# --- Source / connectivity binary sensors ---


class MhCloudAvailableBinarySensor(MhEntity, BinarySensorEntity):
    """True when the cloud is currently feeding data (active_source == 'cloud')."""

    _attr_device_class = "connectivity"
    _attr_icon = "mdi:cloud-check"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Облако доступно"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}cloud_available"

    @property
    def is_on(self) -> bool:
        return getattr(self.coordinator, "active_source", None) == SOURCE_CLOUD


class MhLocalInternetBinarySensor(MhEntity, BinarySensorEntity):
    """Whether the controller itself reports having internet (local /api/getState)."""

    _attr_device_class = "connectivity"
    _attr_icon = "mdi:web"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Контроллер: интернет"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}local_internet"

    @property
    def is_on(self) -> bool | None:
        local = ((self.coordinator.data or {}).get("_local") or {})
        return local.get("inet")