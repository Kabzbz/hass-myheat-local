"""MhEntity class"""

import logging

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_NAME,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
    VERSION,
)
from .coordinator import MhConfigEntry, MhDataUpdateCoordinator

_logger = logging.getLogger(__package__)


def stable_device_key(entry: MhConfigEntry) -> str:
    """Stable per-device key used as the unique_id base.

    Order of preference (whichever is found first):
      1) explicit CONF_DEVICE_KEY in entry.data (set during config / migration)
      2) cloud CONF_DEVICE_ID if non-zero
      3) fallback to entry.entry_id (legacy entries created before migration ran)
    """
    explicit = entry.data.get(CONF_DEVICE_KEY)
    if explicit:
        return str(explicit)
    dev_id = entry.data.get(CONF_DEVICE_ID)
    if dev_id:
        return str(dev_id)
    return entry.entry_id


class MhEntity(CoordinatorEntity[MhDataUpdateCoordinator]):
    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
    ):
        super().__init__(coordinator)
        self.config_entry = config_entry

    @property
    def unique_id(self) -> str:
        return stable_device_key(self.config_entry)

    @property
    def device_info(self) -> DeviceInfo:
        name = self.config_entry.data.get(CONF_NAME, DEFAULT_NAME)
        name += self._mh_dev_name_suffix

        # Получаем данные из координатора для дополнительной информации
        data = self.coordinator.data if self.coordinator.data else {}

        info = DeviceInfo(
            identifiers={self._mh_identifiers},
            name=name,
            model=VERSION,
            manufacturer=MANUFACTURER,
            configuration_url="https://my.myheat.net",
        )

        # Добавляем дополнительную информацию если available
        if data:
            info.update({
                "sw_version": f"Severity: {data.get('severity', 1)}",
                "serial_number": f"DeviceID: {self.config_entry.data.get(CONF_DEVICE_ID)}",
            })

        if self._mh_identifiers != self._mh_via_device:
            info["via_device"] = self._mh_via_device

        return info

    @property
    def _mh_dev_name_suffix(self) -> str:
        return ""

    @property
    def _mh_identifiers(self) -> tuple[str, str]:
        return (DOMAIN, stable_device_key(self.config_entry))

    @property
    def _mh_via_device(self) -> tuple[str, str]:
        return (DOMAIN, stable_device_key(self.config_entry))

    @property
    def _mh_name(self) -> str:
        return self.config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    @property
    def extra_state_attributes(self) -> dict:
        """Return entity specific state attributes."""
        data = self.coordinator.data if self.coordinator.data else {}
        return {
            "attribution": ATTRIBUTION,
            "id": str(self.config_entry.data.get(CONF_DEVICE_ID)),
            "integration": DOMAIN,
            "data_actual": data.get("dataActual", False),
            "system_severity": data.get("severity", 1),
            "system_severity_desc": data.get("severityDesc", ""),
        }


class MhHeaterEntity(MhEntity):
    """Heater element"""

    _key: str | None = None
    heater_name: str = ""
    heater_id: int = 0

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        heater: dict,
    ):
        super().__init__(coordinator, config_entry)
        self.heater_name = heater["name"]
        self.heater_id = heater["id"]

    @property
    def name(self) -> str:
        return (
            f"{self._mh_name} {self.heater_name}{' ' + self._key if self._key else ''}"
        )

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}htr{self.heater_id}{self._key if self._key else ''}"

    @property
    def _mh_dev_name_suffix(self) -> str:
        return f" {self.heater_name}"

    @property
    def _mh_identifiers(self) -> tuple[str, str]:
        return (DOMAIN, f"{super().unique_id}htr{self.heater_id}")

    @property
    def extra_state_attributes(self) -> dict:
        """Return heater specific state attributes."""
        attrs = super().extra_state_attributes
        heater = self.get_heater()
        if heater:
            attrs.update({
                "heater_id": self.heater_id,
                "disabled": heater.get("disabled", False),
                "burner_heating": heater.get("burnerHeating", False),
                "burner_water": heater.get("burnerWater", False),
                "modulation": heater.get("modulation", 0),
            })
        return attrs

    def get_heater(self) -> dict:
        """Return heater state data"""
        if not self.coordinator.data or not self.coordinator.data.get("dataActual", False):
            _logger.warning("data not actual! %s", self.coordinator.data)
            return {}

        for h in self.coordinator.data.get("heaters", []):
            if h["id"] == self.heater_id:
                return h

        return {}


class MhEnvEntity(MhEntity):
    """Env element"""

    _key: str | None = None
    env_name: str = ""
    env_id: int = 0

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        env: dict,
    ):
        super().__init__(coordinator, config_entry)
        self.env_name = env["name"]
        self.env_id = env["id"]

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.env_name}{' ' + self._key if self._key else ''}"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}env{self.env_id}{self._key if self._key else ''}"

    @property
    def _mh_dev_name_suffix(self) -> str:
        return f" {self.env_name}"

    @property
    def _mh_identifiers(self) -> tuple[str, str]:
        return (DOMAIN, f"{super().unique_id}env{self.env_id}")

    @property
    def extra_state_attributes(self) -> dict:
        """Return environment specific state attributes."""
        attrs = super().extra_state_attributes
        env = self.get_env()
        if env:
            attrs.update({
                "env_id": self.env_id,
                "env_type": env.get("type", ""),
                "demand": env.get("demand", False),
                "severity": env.get("severity", 1),
                "severity_desc": env.get("severityDesc", ""),
                "target": env.get("target"),
            })
        return attrs

    def get_env(self) -> dict:
        """Return env state data"""
        if not self.coordinator.data or not self.coordinator.data.get("dataActual", False):
            _logger.warning("data not actual! %s", self.coordinator.data)
            return {}

        for e in self.coordinator.data.get("envs", []):
            if e["id"] == self.env_id:
                return e

        return {}


class MhEngEntity(MhEntity):
    """Eng element"""

    _key: str | None = None
    eng_name: str = ""
    eng_id: int = 0

    def __init__(
        self,
        coordinator: MhDataUpdateCoordinator,
        config_entry: MhConfigEntry,
        eng: dict,
    ):
        super().__init__(coordinator, config_entry)
        self.eng_name = eng["name"]
        self.eng_id = eng["id"]

    @property
    def name(self) -> str:
        return f"{self._mh_name} {self.eng_name}{' ' + self._key if self._key else ''}"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}eng{self.eng_id}{self._key if self._key else ''}"

    @property
    def _mh_dev_name_suffix(self) -> str:
        return f" {self.eng_name}"

    @property
    def _mh_identifiers(self) -> tuple[str, str]:
        return (DOMAIN, f"{super().unique_id}eng{self.eng_id}")

    @property
    def extra_state_attributes(self) -> dict:
        """Return engineering component specific state attributes."""
        attrs = super().extra_state_attributes
        eng = self.get_eng()
        if eng:
            attrs.update({
                "eng_id": self.eng_id,
                "eng_type": eng.get("type", ""),
                "turned_on": eng.get("turnedOn", False),
                "severity": eng.get("severity", 1),
                "severity_desc": eng.get("severityDesc", ""),
            })
        return attrs

    def get_eng(self) -> dict:
        """Return eng state data"""
        if not self.coordinator.data or not self.coordinator.data.get("dataActual", False):
            _logger.warning("data not actual! %s", self.coordinator.data)
            return {}

        for e in self.coordinator.data.get("engs", []):
            if e["id"] == self.eng_id:
                return e

        return {}