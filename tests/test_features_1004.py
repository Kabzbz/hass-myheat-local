"""v10.04: gas from modulation, quiet cloud errors, security switch, target, form."""

import asyncio
import copy
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant import config_entries
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector
from homeassistant.util import slugify
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.myheat import burner as burner_mod
from custom_components.myheat.api import MhApiClient, RPCError
from custom_components.myheat.burner import MhBurnerCoordinator, gas_config
from custom_components.myheat.const import (
    CONF_API_KEY,
    CONF_GAS_RATE,
    CONF_GAS_RATE_MAX,
    CONF_GAS_RATE_MIN,
    CONF_LOCAL_ENABLED,
    CONF_LOCAL_HOST,
    CONF_LOCAL_LOGIN,
    CONF_LOCAL_ONLY,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_POLL_INTERVAL,
    CONF_LOCAL_PROTOCOL,
    CONF_LOCAL_TIMEOUT,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.myheat.local_api import LocalApiError, describe_error
from tests.test_burner import CH_BURNING, DHW_BURNING, FakeLocal
from tests.test_myheat import (
    BASE,
    CLOUD,
    CLOUD_INFO,
    HOST,
    JSON,
    LOCAL_OBJ,
    LOCAL_STATE,
    hybrid_data,
    local_data,
    mock_local,
    set_obj_calls,
    setup,
    states_by_name,
)

GEPARD = {CONF_GAS_RATE_MIN: 1.05, CONF_GAS_RATE_MAX: 2.80}   # Protherm «Гепард» 24
MID = (1.05 + 2.80) / 2
OURS = "custom_components.myheat"


def patch_clock(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(burner_mod.time, "monotonic", lambda: clock["t"])
    return clock


def cloud_info(**heater):
    info = copy.deepcopy(CLOUD_INFO)
    info["data"]["heaters"][0].update(heater)
    return info


def mock_local_obj(aioclient_mock, obj):
    aioclient_mock.post(f"{BASE}/api/login", json={"status": True}, headers=JSON)
    aioclient_mock.post(f"{BASE}/api/getState", json=LOCAL_STATE, headers=JSON)
    aioclient_mock.post(f"{BASE}/api/getObjState", json=obj, headers=JSON)


# --- 1. gas from modulation: the formula --------------------------------------

class FakeMain:
    """The main coordinator as the burner poller sees it."""

    def __init__(self, source="cloud", actual=True, **heater):
        self.active_source = source
        self.data = {"dataActual": actual, "heaters": [{"id": 45, **heater}]}


async def burn(hass, monkeypatch, main, flags, data=GEPARD):
    """Two samples 15 s apart, both burning; returns the second one."""
    entry = MockConfigEntry(domain=DOMAIN, data=dict(data))
    entry.add_to_hass(hass)
    coordinator = MhBurnerCoordinator(hass, entry, FakeLocal([flags, flags]), 15, main=main)
    for t in (0, 15):
        monkeypatch.setattr(burner_mod.time, "monotonic", lambda t=t: float(t))
        sample = await coordinator._async_update_data()
    return sample


def test_gas_config():
    assert gas_config({}) == (0.0, None, None)
    assert gas_config(GEPARD) == (0.0, 1.05, 2.80)
    assert gas_config({CONF_GAS_RATE_MIN: 2.8, CONF_GAS_RATE_MAX: 1.05}) == (0.0, None, None)
    assert gas_config({CONF_GAS_RATE_MIN: 1.05}) == (0.0, None, None)
    assert gas_config({CONF_GAS_RATE: "2.4", **GEPARD}) == (2.4, 1.05, 2.80)


async def test_gas_hot_water_at_full_power(hass, monkeypatch):
    d = await burn(hass, monkeypatch, FakeMain(burnerWater=True, modulation=100), DHW_BURNING)
    assert d["gas_rate"] == pytest.approx(2.80) and d["gas_modulation"] == 100
    assert d["gas_m3"] == pytest.approx(15 * 2.80 / 3600)
    assert d["gas_dhw_m3"] == pytest.approx(15 * 2.80 / 3600)
    assert d["gas_ch_m3"] == 0


async def test_gas_heating_at_half_power(hass, monkeypatch):
    d = await burn(hass, monkeypatch, FakeMain(burnerHeating=True, modulation=50), CH_BURNING)
    rate = 1.05 + (2.80 - 1.05) * 0.5
    assert d["gas_rate"] == pytest.approx(rate)
    assert d["gas_ch_m3"] == pytest.approx(15 * rate / 3600)
    assert d["gas_dhw_m3"] == 0


async def test_gas_minimum_power_is_min_rate(hass, monkeypatch):
    """0 % from a snapshot taken while burning is the minimum, not "off"."""
    d = await burn(hass, monkeypatch, FakeMain(burnerHeating=True, modulation=0), CH_BURNING)
    assert d["gas_rate"] == pytest.approx(1.05) and d["gas_modulation"] == 0


async def test_gas_modulation_clamped(hass, monkeypatch):
    d = await burn(hass, monkeypatch, FakeMain(burnerHeating=True, modulation=150), CH_BURNING)
    assert d["gas_rate"] == pytest.approx(2.80)


@pytest.mark.parametrize(
    "main",
    [
        FakeMain(source="local", burnerHeating=True, modulation=100),    # cloud not the source
        FakeMain(actual=False, burnerHeating=True, modulation=100),      # stale cloud data
        FakeMain(burnerHeating=False, burnerWater=False, modulation=0),  # snapshot before the burn
        FakeMain(burnerHeating=True, modulation=None),                   # no value
        None,                                                            # no main coordinator
    ],
    ids=["local-source", "stale", "before-ignition", "no-value", "no-main"],
)
async def test_gas_unknown_modulation_uses_mid_range(hass, monkeypatch, main):
    d = await burn(hass, monkeypatch, main, CH_BURNING)
    assert d["gas_modulation"] is None
    assert d["gas_rate"] == pytest.approx(MID)


async def test_gas_unknown_modulation_prefers_average_rate(hass, monkeypatch):
    d = await burn(hass, monkeypatch, None, CH_BURNING, data={CONF_GAS_RATE: 2.4, **GEPARD})
    assert d["gas_rate"] == pytest.approx(2.4)


async def test_gas_without_range_uses_rate_as_before(hass, monkeypatch):
    main = FakeMain(burnerHeating=True, modulation=100)
    d = await burn(hass, monkeypatch, main, CH_BURNING, data={CONF_GAS_RATE: 2.4})
    assert (d["gas_rate"], d["gas_modulation"]) == (pytest.approx(2.4), None)
    assert d["gas_m3"] == pytest.approx(15 * 2.4 / 3600)


async def test_no_gas_when_not_configured(hass, monkeypatch):
    d = await burn(hass, monkeypatch, FakeMain(burnerHeating=True, modulation=50), CH_BURNING, data={})
    assert d["gas_m3"] == 0 and d["gas_rate"] is None


# --- 1. gas from modulation: in Home Assistant --------------------------------

async def test_gas_sensors_follow_cloud_modulation(hass, aioclient_mock, monkeypatch):
    clock = patch_clock(monkeypatch)
    mock_local(aioclient_mock)                              # controller: burning for heating
    aioclient_mock.post(CLOUD, json=cloud_info(modulation=50))
    data = hybrid_data()
    data.update(GEPARD)
    entry = await setup(hass, data)
    assert entry.runtime_data.active_source == "cloud"
    clock["t"] += 15
    await entry.runtime_data.burner.async_refresh()
    await hass.async_block_till_done()

    rate = 1.05 + (2.80 - 1.05) * 0.5
    st = states_by_name(hass)
    total = st["myheat Котел Газ (оценка)"]
    assert total.entity_id == "sensor.myheat_kotel_gaz_otsenka"     # key unchanged
    assert total.attributes["device_class"] == "gas"
    assert total.attributes["state_class"] == "total_increasing"
    assert float(total.state) == pytest.approx(15 * rate / 3600, abs=1e-5)
    assert total.attributes["расход_м3_ч"] == pytest.approx(rate, abs=1e-3)
    assert total.attributes["модуляция"] == 50
    heating = st["myheat Котел Газ на отопление (оценка)"]
    assert float(heating.state) == pytest.approx(15 * rate / 3600, abs=1e-5)
    assert float(st["myheat Котел Газ на ГВС (оценка)"].state) == 0


async def test_gas_sensors_with_range_only_local(hass, aioclient_mock, monkeypatch):
    """Local only: no modulation at all -> the middle of the range."""
    clock = patch_clock(monkeypatch)
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data(**GEPARD))
    clock["t"] += 15
    await entry.runtime_data.burner.async_refresh()
    await hass.async_block_till_done()
    total = hass.states.get("sensor.myheat_192_168_1_50_kotel_gaz_otsenka")
    assert float(total.state) == pytest.approx(15 * MID / 3600, abs=1e-5)
    assert total.attributes["модуляция"] is None


async def test_no_gas_sensors_with_half_range(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data(**{CONF_GAS_RATE_MIN: 1.05}))
    assert not [s for s in hass.states.async_all() if "_gaz_" in s.entity_id]


# --- 2. cloud errors in the log ------------------------------------------------

def ours(caplog):
    return [r for r in caplog.records if r.name.startswith(OURS)]


def test_describe_error():
    assert describe_error(TimeoutError()) == "TimeoutError"
    assert describe_error(LocalApiError("/api/getObjState: TimeoutError")) == (
        "LocalApiError: /api/getObjState: TimeoutError"
    )
    assert str(RPCError({"err": 3})) == "err=3"


async def test_cloud_timeout_warns_once_without_traceback(hass, aioclient_mock, caplog):
    caplog.set_level(logging.DEBUG, logger=OURS)
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, exc=TimeoutError())
    entry = await setup(hass, hybrid_data())
    coordinator = entry.runtime_data
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.active_source == "local"

    records = ours(caplog)
    assert not [r for r in records if r.levelno >= logging.ERROR]
    assert not [r for r in records if r.exc_info]
    assert [r.getMessage() for r in records if r.levelno == logging.WARNING] == [
        "MyHeat cloud: getDeviceInfo failed: no answer in 10 s"
    ]
    assert [r.getMessage() for r in records if "Switched to LOCAL" in r.getMessage()] == [
        "Switched to LOCAL source (cloud: TimeoutError)"
    ]
    assert "cloud poll failed: TimeoutError" in [r.getMessage() for r in records]

    aioclient_mock.clear_requests()
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await coordinator.async_refresh()
    assert coordinator.active_source == "cloud"
    messages = [r.getMessage() for r in ours(caplog)]
    assert "MyHeat cloud answers again" in messages
    assert "Switched back to CLOUD source" in messages


async def test_cloud_refusal_is_a_warning(hass, aioclient_mock, caplog):
    aioclient_mock.post(CLOUD, json={"err": 3})
    client = MhApiClient(
        username="u", api_key="k", device_id=1, session=async_get_clientsession(hass)
    )
    with pytest.raises(RPCError):
        await client.rpc("getDeviceInfo", deviceId=None)
    records = [r for r in ours(caplog) if r.levelno >= logging.INFO]
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("WARNING", "MyHeat cloud: getDeviceInfo failed: refused, err=3")
    ]
    assert records[0].exc_info is None


async def test_cancelled_is_not_a_timeout(caplog):
    session = MagicMock()
    session.post = AsyncMock(side_effect=asyncio.CancelledError())
    client = MhApiClient(username="u", api_key="k", device_id=1, session=session)
    with pytest.raises(asyncio.CancelledError):
        await client.rpc("getDeviceInfo", deviceId=None)
    assert ours(caplog) == []


async def test_both_sources_down_names_the_errors(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE}/api/login", exc=TimeoutError())
    aioclient_mock.post(CLOUD, exc=TimeoutError())
    data = hybrid_data()
    entry = MockConfigEntry(domain=DOMAIN, version=2, data=data, unique_id="x")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    assert "cloud: TimeoutError" in entry.reason
    assert "local: LocalApiError: login failed: TimeoutError" in entry.reason


# --- 3/4. units and the target sensor -----------------------------------------

async def test_target_sensor_off_by_default(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    target = er.async_get(hass).async_get("sensor.myheat_kotel_tselevaia")
    assert target is not None
    assert target.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.myheat_kotel_tselevaia") is None


async def test_counters_have_a_unit(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    for key in ("rozzhigi_gorelki", "vkliucheniia_gvs"):
        st = hass.states.get(f"sensor.myheat_192_168_1_50_kotel_{key}")
        assert st.attributes["unit_of_measurement"] == "раз", key
        assert st.attributes["state_class"] == "total_increasing"


# --- 5. security switch -------------------------------------------------------

async def test_security_follows_controller_web_ui(hass, aioclient_mock):
    """No securityArmed from the controller: its web UI offers to arm -> off."""
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    st = states_by_name(hass)["myheat Охрана"]
    assert st.state == "off"
    assert "assumed_state" not in st.attributes
    assert st.attributes["источник"] == "контроллер"


async def test_security_armed_on_controller(hass, aioclient_mock):
    obj = copy.deepcopy(LOCAL_OBJ)
    obj["securityArmed"] = True
    mock_local_obj(aioclient_mock, obj)
    await setup(hass, local_data())
    assert states_by_name(hass)["MyHeat (192.168.1.50) Охрана"].state == "on"


async def test_security_arm_locally_confirmed_by_controller(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    entity_id = states_by_name(hass)["MyHeat (192.168.1.50) Охрана"].entity_id
    armed = copy.deepcopy(LOCAL_OBJ)
    armed["securityArmed"] = True
    aioclient_mock.clear_requests()
    mock_local_obj(aioclient_mock, armed)
    aioclient_mock.post(f"{BASE}/api/setObjState", json={"status": 1}, headers=JSON)
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()
    assert set_obj_calls(aioclient_mock) == [{"action": "armSecurity"}]
    st = hass.states.get(entity_id)
    assert st.state == "on"
    assert st.attributes["источник"] == "контроллер"


async def test_security_cloud_only_remembers_last_command(hass, aioclient_mock):
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    data = hybrid_data()
    data.update({CONF_LOCAL_ENABLED: False, CONF_LOCAL_ONLY: False})
    await setup(hass, data)
    st = states_by_name(hass)["myheat Охрана"]
    assert st.state == "unknown"                  # the cloud cannot report it
    assert st.attributes["assumed_state"] is True  # HA shows on/off buttons
    await hass.services.async_call("switch", "turn_on", {"entity_id": st.entity_id}, blocking=True)
    await hass.async_block_till_done()
    st = hass.states.get(st.entity_id)
    assert st.state == "on"
    assert st.attributes["источник"] == "последняя команда"
    sent = [c[2] for c in aioclient_mock.mock_calls
            if isinstance(c[2], dict) and c[2].get("action") == "setSecurityMode"]
    assert [s["mode"] for s in sent] == [1]


async def test_security_last_command_restored(hass, aioclient_mock):
    entity_id = f"switch.{slugify('myheat Охрана')}"
    mock_restore_cache(hass, (State(entity_id, "on"),))
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    data = hybrid_data()
    data.update({CONF_LOCAL_ENABLED: False, CONF_LOCAL_ONLY: False})
    await setup(hass, data)
    assert hass.states.get(entity_id).state == "on"


# --- 6. settings form ---------------------------------------------------------

BASE_OPTIONS = {
    CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True, CONF_LOCAL_HOST: HOST,
    CONF_LOCAL_LOGIN: "myheat", CONF_LOCAL_PASSWORD: "myheat",
    CONF_LOCAL_PROTOCOL: "http", CONF_LOCAL_POLL_INTERVAL: 30, CONF_LOCAL_TIMEOUT: 30,
}


def fields(result) -> dict:
    return {str(key): (key, selector) for key, selector in result["data_schema"].schema.items()}


@pytest.mark.parametrize(("language", "sec", "flow"), [("en", "s", "m³/h"), ("ru", "с", "м³/ч")])
async def test_options_form_selectors(hass, aioclient_mock, language, sec, flow):
    hass.config.language = language
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data(**GEPARD))
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form = fields(result)
    _, password = form["local_password"]
    assert isinstance(password, TextSelector)
    assert password.config["type"] == "password"
    for key in ("local_poll_interval", "local_timeout", "burner_poll_interval"):
        assert form[key][1].config["unit_of_measurement"] == sec, key
    for key in ("gas_rate", "gas_rate_min", "gas_rate_max"):
        assert form[key][1].config["unit_of_measurement"] == flow, key
    # saved values are pre-filled, an unset one stays empty
    assert form["gas_rate_min"][0].description == {"suggested_value": 1.05}
    assert form["gas_rate"][0].description == {"suggested_value": None}


async def test_options_gas_range(hass, aioclient_mock):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for bad in ({CONF_GAS_RATE_MIN: 1.05}, {CONF_GAS_RATE_MIN: 2.8, CONF_GAS_RATE_MAX: 1.05}):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={**BASE_OPTIONS, **bad}
        )
        assert result["type"] == "form"
        assert result["errors"] == {"base": "gas_range_invalid"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={**BASE_OPTIONS, **GEPARD}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert (entry.data[CONF_GAS_RATE_MIN], entry.data[CONF_GAS_RATE_MAX]) == (1.05, 2.80)
    assert entry.data[CONF_GAS_RATE] == 0
    assert entry.data[CONF_LOCAL_POLL_INTERVAL] == 30
    assert hass.states.get("sensor.myheat_192_168_1_50_kotel_gaz_otsenka") is not None


async def test_config_flow_local_step_form(hass, aioclient_mock):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "", CONF_API_KEY: "",
                    CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True},
    )
    assert result["step_id"] == "local"
    form = fields(result)
    assert form["local_password"][1].config["type"] == "password"
    assert form["burner_poll_interval"][1].config["unit_of_measurement"] == "s"
    assert (form["local_timeout"][1].config["min"], form["local_timeout"][1].config["max"]) == (10, 120)
    hass.config_entries.flow.async_abort(result["flow_id"])
