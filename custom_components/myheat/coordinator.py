from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MhApiClient
from .const import (  # noqa: F401
    CONF_LOCAL_ENABLED,
    CONF_LOCAL_ONLY,
    CONF_LOCAL_POLL_INTERVAL,
    DEFAULT_CLOUD_POLL_INTERVAL,
    DEFAULT_LOCAL_POLL_INTERVAL,
    DOMAIN,
    SOURCE_CLOUD,
    SOURCE_LOCAL,
    SOURCE_OFFLINE,
)
from .local_api import (
    LocalApiError,
    MhLocalApiClient,
    translate_local_to_cloud,
)

_LOGGER = logging.getLogger(__package__)

SCAN_INTERVAL = timedelta(seconds=DEFAULT_CLOUD_POLL_INTERVAL)

# How often to background-refresh local /api/getState (Баланс SIM, Сигнал GSM,
# WiFi SSID, Контроллер: интернет) when the cloud is the active source.
LOCAL_STATE_REFRESH_SECONDS = 600  # 10 minutes

type MhConfigEntry = ConfigEntry[MhDataUpdateCoordinator]


class MhDataUpdateCoordinator(DataUpdateCoordinator[dict]):
    """Fetches data from the MyHeat cloud, with optional local fallback.

    Behaviour driven by entry.data:
      * local_enabled=False, local_only=False  -> cloud only (legacy)
      * local_enabled=True,  local_only=False  -> cloud first, fall back to
        local on cloud failure; auto-recover when cloud is back
      * local_only=True                        -> local only (no cloud calls)

    Always exposes self.active_source ∈ {"cloud", "local", "offline"} so
    sensors can show what's currently feeding HA.
    """

    api: MhApiClient | None
    local_api: MhLocalApiClient | None
    active_source: str

    def __init__(
        self,
        hass: HomeAssistant,
        entry: MhConfigEntry,
        client: MhApiClient | None,
        local_client: MhLocalApiClient | None = None,
    ) -> None:
        self.api = client
        self.local_api = local_client
        self.active_source = SOURCE_OFFLINE
        self._local_only = bool(entry.data.get(CONF_LOCAL_ONLY))
        self._local_enabled = self._local_only or bool(
            entry.data.get(CONF_LOCAL_ENABLED)
        )
        # Cached _local block (Баланс SIM, GSM, WiFi, inet, serial) so that
        # local-only sensors keep working when the active source is cloud.
        self._local_cache: dict | None = None
        self._local_cache_at: float = 0.0
        self._cloud_interval = timedelta(seconds=DEFAULT_CLOUD_POLL_INTERVAL)
        self._local_interval = timedelta(
            seconds=int(
                entry.data.get(CONF_LOCAL_POLL_INTERVAL, DEFAULT_LOCAL_POLL_INTERVAL)
            )
        )
        initial_interval = (
            self._local_interval if self._local_only else self._cloud_interval
        )

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=initial_interval,
            always_update=True,
        )

    @property
    def local_enabled(self) -> bool:
        return self._local_enabled

    @property
    def local_only(self) -> bool:
        return self._local_only

    async def _fetch_local(self) -> dict:
        """Read both /api/getState and /api/getObjState; return cloud-shaped dict.

        Also refreshes the local-state cache so cloud-mode polls reuse it.
        """
        if self.local_api is None:
            raise LocalApiError("local client is not configured")
        state = await self.local_api.async_get_state()
        obj_state = await self.local_api.async_get_obj_state()
        data = translate_local_to_cloud(state=state, obj_state=obj_state)
        self._local_cache = data["_local"]
        self._local_cache_at = time.monotonic()
        return data

    async def _refresh_local_cache_if_due(self) -> None:
        """Background-poll local /api/getState if the cache is stale.

        Called from cloud-mode updates so that Баланс SIM / GSM / WiFi sensors
        get fresh values without hammering the controller every 30 s.
        """
        if not self._local_enabled or self.local_api is None:
            return
        now = time.monotonic()
        if (
            self._local_cache is not None
            and (now - self._local_cache_at) < LOCAL_STATE_REFRESH_SECONDS
        ):
            return
        try:
            state = await self.local_api.async_get_state()
        except LocalApiError as err:
            _LOGGER.debug("background local-state refresh failed: %s", err)
            return
        translated = translate_local_to_cloud(state=state, obj_state=None)
        self._local_cache = translated["_local"]
        self._local_cache_at = now

    def _apply_interval_for_source(self) -> None:
        """Adjust update_interval to match the currently active source."""
        target = (
            self._local_interval
            if self.active_source == SOURCE_LOCAL
            else self._cloud_interval
        )
        if self.update_interval != target:
            self.update_interval = target

    async def _async_update_data(self) -> dict:
        """Update via cloud first; fall back to local if enabled."""
        if self._local_only:
            try:
                data = await self._fetch_local()
                self.active_source = SOURCE_LOCAL
                self._apply_interval_for_source()
                return data
            except LocalApiError as err:
                self.active_source = SOURCE_OFFLINE
                raise UpdateFailed(f"local: {err}") from err

        # Try cloud
        cloud_err: Exception | None = None
        if self.api is not None:
            try:
                data = await self.api.async_get_device_info()
                self.active_source = SOURCE_CLOUD
                self._apply_interval_for_source()
                # Even in cloud mode, refresh local-state cache every 10 min
                # so that Баланс SIM/GSM/WiFi/inet sensors keep showing values.
                await self._refresh_local_cache_if_due()
                if self._local_cache is not None:
                    data["_local"] = self._local_cache
                return data
            except Exception as err:  # noqa: BLE001 — cloud client raises many types
                cloud_err = err
                _LOGGER.warning("cloud poll failed: %s", err)

        # Cloud failed; try local fallback if user enabled it
        if self._local_enabled and self.local_api is not None:
            try:
                data = await self._fetch_local()
                self.active_source = SOURCE_LOCAL
                self._apply_interval_for_source()
                _LOGGER.info("Switched to LOCAL source (cloud is down)")
                return data
            except LocalApiError as err:
                self.active_source = SOURCE_OFFLINE
                raise UpdateFailed(
                    f"both cloud and local failed (cloud: {cloud_err}; local: {err})"
                ) from err

        self.active_source = SOURCE_OFFLINE
        raise UpdateFailed(f"cloud: {cloud_err}")
