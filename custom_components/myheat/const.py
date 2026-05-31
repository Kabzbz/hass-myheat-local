"""Constants for MyHeat."""

from homeassistant.const import CONF_API_KEY  # noqa: F401
from homeassistant.const import CONF_DEVICE_ID  # noqa: F401
from homeassistant.const import CONF_NAME  # noqa: F401
from homeassistant.const import CONF_USERNAME  # noqa: F401
from homeassistant.const import Platform

# Base component constants
NAME = "MyHeat.net"
DOMAIN = "myheat"
DOMAIN_DATA = f"{DOMAIN}_data"
VERSION = "9.05"

ATTRIBUTION = "https://myheat.net"
MANUFACTURER = "https://myheat.net"

ISSUE_URL = "https://github.com/vooon/hass-myheat/issues"


# Local API config keys
CONF_LOCAL_ENABLED = "local_enabled"
CONF_LOCAL_ONLY = "local_only"
CONF_LOCAL_HOST = "local_host"
CONF_LOCAL_LOGIN = "local_login"
CONF_LOCAL_PASSWORD = "local_password"
CONF_LOCAL_PROTOCOL = "local_protocol"
CONF_LOCAL_POLL_INTERVAL = "local_poll_interval"
CONF_LOCAL_TIMEOUT = "local_timeout"

# Stable per-device key used as basis for entity unique_id (survives entry re-create).
# For cloud entries it equals str(device_id); for local-only entries — f"local_{serial}".
CONF_DEVICE_KEY = "device_key"

DEFAULT_LOCAL_HOST = "192.168.1.50"
DEFAULT_LOCAL_LOGIN = "myheat"
DEFAULT_LOCAL_PASSWORD = "myheat"
DEFAULT_LOCAL_PROTOCOL = "http"
DEFAULT_LOCAL_POLL_INTERVAL = 30   # seconds; controller is slow, don't go below 15
DEFAULT_CLOUD_POLL_INTERVAL = 30   # seconds
DEFAULT_LOCAL_TIMEOUT = 30         # seconds per request

LOCAL_PROTOCOLS = ["http", "https"]

SOURCE_CLOUD = "cloud"
SOURCE_LOCAL = "local"
SOURCE_OFFLINE = "offline"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
BINARY_SENSOR = "binary_sensor"
SENSOR = "sensor"
SWITCH = "switch"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

# Defaults
DEFAULT_NAME = DOMAIN


STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
