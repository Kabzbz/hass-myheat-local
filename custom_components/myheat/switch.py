"""Switch platform for MyHeat."""

import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import MhConfigEntry, MhDataUpdateCoordinator
from .entity import MhEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MhConfigEntry,
    async_add_devices: AddConfigEntryEntitiesCallback,
):
    """Setup sensor platform."""
    coordinator: MhDataUpdateCoordinator = entry.runtime_data
    async_add_devices([MhSecuritySwitch(coordinator, entry)])


class MhSecuritySwitch(MhEntity, SwitchEntity, RestoreEntity):
    """Security mode ("Охрана").

    The cloud API can only set it. The controller reports it in
    getObjState.securityArmed (local API), and its web UI offers
    "Поставить на охрану" unless that is true — the switch shows the same.
    Without the local API the state is the last command sent from HA.
    """

    _attr_icon = "mdi:security"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._assumed: bool | None = None
        # time.monotonic() of the last command: older controller snapshots
        # don't know about it yet
        self._command_at = 0.0

    @property
    def name(self) -> str:
        return f"{self._mh_name} Охрана"

    @property
    def unique_id(self) -> str:
        return f"{super().unique_id}security"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._assumed = last.state == "on"
        burner = getattr(self.coordinator, "burner", None)
        if burner is not None:
            # the fast poller re-reads the controller every few seconds
            self.async_on_remove(
                burner.async_add_listener(self._handle_coordinator_update)
            )

    def _controller_armed(self) -> bool | None:
        """Armed state as the controller reports it; None when unknown."""
        burner = getattr(self.coordinator, "burner", None)
        if (
            burner is not None
            and burner.last_obj is not None
            and burner.last_obj_at > self._command_at
        ):
            return burner.last_obj.get("securityArmed") is True
        local = (self.coordinator.data or {}).get("_local")
        if local is not None and self.coordinator.local_cache_at > self._command_at:
            return local.get("securityArmed") is True
        return None

    @property
    def is_on(self) -> bool | None:
        armed = self._controller_armed()
        return self._assumed if armed is None else armed

    @property
    def assumed_state(self) -> bool:
        # HA then shows separate "on"/"off" buttons instead of a toggle
        return self._controller_armed() is None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = super().extra_state_attributes
        attrs["источник"] = (
            "последняя команда" if self._controller_armed() is None else "контроллер"
        )
        return attrs

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        await self._async_set(True)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        await self._async_set(False)

    async def _async_set(self, armed: bool) -> None:
        await self.coordinator.api.async_set_security_mode(mode=armed)
        self._assumed = armed
        self._command_at = time.monotonic()
        self.async_write_ha_state()
        if self.coordinator.local_enabled:
            self.coordinator.mark_local_stale()
            burner = getattr(self.coordinator, "burner", None)
            if burner is not None:
                await burner.async_request_refresh()
