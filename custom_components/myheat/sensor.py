"""Sensor platform for MyHeat."""

from itertools import chain

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTemperature, UnitOfPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SOURCE_LOCAL, SOURCE_OFFLINE
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .entity import MhEntity, MhHeaterEntity, MhEnvEntity, MhEngEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MhConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: MhDataUpdateCoordinator = entry.runtime_data

    extras: list = [MhActiveSourceSensor(coordinator, entry)]
    local = (coordinator.data or {}).get("_local") or {}
    if coordinator.local_enabled:
        extras.extend(
            [
                MhSimBalanceSensor(coordinator, entry),
                MhGsmSignalSensor(coordinator, entry),
                MhWifiSsidSensor(coordinator, entry),
            ]
        )

    entities = chain(
        tuple(extras),
        (MhWeatherTempSensor(coordinator, entry),),
        (MhSeveritySensor(coordinator, entry),),
        chain.from_iterable(
            [
                MhHeaterFlowTempSensor(coordinator, entry, heater),
                MhHeaterReturnTempSensor(coordinator, entry, heater),
                MhHeaterTargetTempSensor(coordinator, entry, heater),
                MhHeaterPressureSensor(coordinator, entry, heater),
                MhHeaterModulationSensor(coordinator, entry, heater),
            ]
            for heater in coordinator.data.get("heaters", [])
        ),
        (
            MhEnvTempSensor(coordinator, entry, env)
            for env in coordinator.data.get("envs", [])
            if env.get("type") in ["room_temperature", "circuit_temperature", "boiler_temperature", "temperature"]
        ),
        (
            MhEngStateSensor(coordinator, entry, eng)
            for eng in coordinator.data.get("engs", [])
        ),
        (
            MhAdditionalTempSensor(coordinator, entry, env)
            for env in coordinator.data.get("envs", [])
            if env.get("type") not in ["room_temperature", "circuit_temperature", "boiler_temperature", "temperature"]
            and isinstance(env.get("value"), (int, float))
        ),
    )

    async_add_entities(entities)


class MhWeatherTempSensor(MhEntity, SensorEntity):
    """myheat weatherTemp Sensor class."""

    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:weather-cloudy"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Уличная температура"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}weather_temp"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("weatherTemp")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "город": self.coordinator.data.get("city", ""),
            "актуальные_данные": self.coordinator.data.get("dataActual", False),
        }


class MhSeveritySensor(MhEntity, SensorEntity):
    """myheat severity Sensor class."""

    _attr_icon = "mdi:alert-circle-outline"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Статус системы"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}system_severity"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("severityDesc")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "код_статуса": self.coordinator.data.get("severity", 1),
            "актуальные_данные": self.coordinator.data.get("dataActual", False),
            "количество_тревог": len(self.coordinator.data.get("alarms", [])),
        }


class MhHeaterSensor(MhHeaterEntity, SensorEntity):
    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        return self.get_heater().get(self._key)


class MhHeaterFlowTempSensor(MhHeaterSensor):
    _key = "flowTemp"
    _attr_icon = "mdi:thermometer-chevron-up"
    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Подача"


class MhHeaterReturnTempSensor(MhHeaterSensor):
    _key = "returnTemp"
    _attr_icon = "mdi:thermometer-chevron-down"
    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Обратка"


class MhHeaterTargetTempSensor(MhHeaterSensor):
    _key = "targetTemp"
    _attr_icon = "mdi:thermometer-auto"
    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Целевая"


class MhHeaterPressureSensor(MhHeaterSensor):
    _key = "pressure"
    _attr_icon = "mdi:gauge"
    _attr_device_class = "pressure"
    _attr_native_unit_of_measurement = UnitOfPressure.BAR

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Давление"


class MhHeaterModulationSensor(MhHeaterSensor):
    _key = "modulation"
    _attr_icon = "mdi:fire"
    _attr_native_unit_of_measurement = "%"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.heater_name} Модуляция"

    @property
    def extra_state_attributes(self):
        heater = self.get_heater()
        return {
            "горелка_отопление": heater.get("burnerHeating", False),
            "горелка_гвс": heater.get("burnerWater", False),
            "температура_подачи": heater.get("flowTemp"),
            "температура_обратки": heater.get("returnTemp"),
        }


class MhEnvTempSensor(MhEnvEntity, SensorEntity):
    """Environment temperature sensor"""
    
    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer"

    @property
    def name(self) -> str:
        env_type = self.get_env().get("type", "")
        type_map = {
            "room_temperature": "Помещение",
            "circuit_temperature": "Контур",
            "boiler_temperature": "Бойлер",
            "temperature": "Температура"
        }
        type_name = type_map.get(env_type, "Температура")
        return f"{self._mh_name} {self.env_name} {type_name}"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}env{self.env_id}_temp"

    @property
    def native_value(self) -> float | None:
        return self.get_env().get("value")

    @property
    def extra_state_attributes(self) -> dict:
        e = self.get_env()
        return {
            "целевая_температура": e.get("target"),
            "запрос_тепла": e.get("demand", False),
            "тип_среды": e.get("type", ""),
            "код_статуса": e.get("severity", 1),
            "статус": e.get("severityDesc", ""),
        }


class MhAdditionalTempSensor(MhEnvEntity, SensorEntity):
    """Additional temperature sensors that are not main environments"""
    
    _attr_device_class = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        env: dict,
    ):
        super().__init__(coordinator, config_entry, env)
        self.env_type = env.get("type", "unknown")

    @property
    def name(self) -> str:
        type_map = {
            "outdoor_temperature": "Уличная",
            "water_temperature": "Вода",
            "floor_temperature": "Теплый пол",
            "return_temperature": "Обратка",
            "flow_temperature": "Подача",
            "unknown": "Датчик"
        }
        type_name = type_map.get(self.env_type, self.env_type.replace("_", " ").title())
        return f"{self._mh_name} {self.env_name} {type_name}"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}env{self.env_id}_additional_temp"

    @property
    def native_value(self) -> float | None:
        return self.get_env().get("value")

    @property
    def extra_state_attributes(self) -> dict:
        e = self.get_env()
        return {
            "тип_датчика": e.get("type", ""),
            "код_статуса": e.get("severity", 1),
            "статус": e.get("severityDesc", ""),
            "id_датчика": self.env_id,
        }


class MhEngStateSensor(MhEngEntity, SensorEntity):
    """Engineering component state sensor"""
    
    _attr_icon = "mdi:cog"

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.eng_name} Состояние"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}eng{self.eng_id}_state"

    @property
    def native_value(self) -> str | None:
        eng = self.get_eng()
        turned_on = eng.get("turnedOn", False)
        return "Включено" if turned_on else "Выключено"

    @property
    def extra_state_attributes(self) -> dict:
        e = self.get_eng()
        return {
            "тип_оборудования": e.get("type", ""),
            "код_статуса": e.get("severity", 1),
            "статус": e.get("severityDesc", ""),
            "включено": e.get("turnedOn", False),
        }


# --- Source / local-only diagnostics ---


class MhActiveSourceSensor(MhEntity, SensorEntity):
    """Shows which API is currently feeding data: cloud / local / offline."""

    _attr_icon = "mdi:cloud-sync"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Источник данных"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}active_source"

    @property
    def native_value(self) -> str:
        return getattr(self.coordinator, "active_source", SOURCE_OFFLINE)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "локальный_включен": getattr(self.coordinator, "local_enabled", False),
            "только_локально": getattr(self.coordinator, "local_only", False),
        }


class _LocalAttrSensor(MhEntity, SensorEntity):
    """Base for sensors that read fields from data['_local']."""

    _local_key: str = ""

    @property
    def _local(self) -> dict:
        return ((self.coordinator.data or {}).get("_local") or {})

    @property
    def native_value(self):
        return self._local.get(self._local_key)


class MhSimBalanceSensor(_LocalAttrSensor):
    _local_key = "gsmBalance"
    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = "RUB"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Баланс SIM"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}sim_balance"


class MhGsmSignalSensor(_LocalAttrSensor):
    _local_key = "gsmRssi"
    _attr_icon = "mdi:signal"

    @property
    def name(self) -> str:
        return f"{self._mh_name} Сигнал GSM"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}gsm_signal"


class MhWifiSsidSensor(_LocalAttrSensor):
    _local_key = "wifiSsid"
    _attr_icon = "mdi:wifi"

    @property
    def name(self) -> str:
        return f"{self._mh_name} WiFi SSID"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}wifi_ssid"