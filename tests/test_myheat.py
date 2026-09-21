"""Runtime tests for the MyHeat integration against a real Home Assistant core.

Local API responses are payloads captured from a real controller (identifiers replaced).
"""

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myheat.const import (
    CONF_API_KEY,
    CONF_DEVICE_ID,
    CONF_DEVICE_KEY,
    CONF_LOCAL_ENABLED,
    CONF_LOCAL_HOST,
    CONF_LOCAL_LOGIN,
    CONF_LOCAL_ONLY,
    CONF_LOCAL_PASSWORD,
    CONF_LOCAL_POLL_INTERVAL,
    CONF_LOCAL_PROTOCOL,
    CONF_LOCAL_TIMEOUT,
    CONF_NAME,
    CONF_USERNAME,
    DOMAIN,
)

HOST = "192.168.1.50"
BASE = f"http://{HOST}"
CLOUD = "https://my.myheat.net/api/request/"
SERIAL = "12345678901234"

LOCAL_STATE = {
    "status": 1, "inet": "0", "serial": SERIAL, "regkey": "00000000",
    "wifiSsid": "HomeWiFi", "gsmCarrier": "", "gsmRssi": "65", "gsmBalance": "-1.61",
}

LOCAL_OBJ = {
    "deviceFlags": 24833, "simSignal": 68, "simBalance": -1.61, "sched": 1,
    "envs": [
        {"n": "Температура в бойлерной", "i": 69, "t": 112, "f": 0, "sev": 1,
         "st": {"p1": 22.9375, "p4": 0}, "s": {"p3001": "heating_circuit"}},
        {"n": "Температура батареи обратка ", "i": 70, "t": 112, "f": 0, "sev": 1,
         "st": {"p1": 41.9375, "p4": 0}, "s": {"p3001": "heating_circuit"}},
        # st/s values as on the real controller: goal lives in s.p3008,
        # st.p4 is a 0/1 flag; DHW has no temperature probe.
        {"n": "Контур ГВС", "i": 47, "t": 103, "f": 0, "sev": 0,
         "st": {"p1": -16777216, "p4": 1},
         "s": {"p3001": "dhw_circuit", "p3008": "60", "p3011": "65", "p3012": "40"}},
        {"n": "Температура помещения", "i": 48, "t": 101, "f": 128, "sev": 1,
         "st": {"p1": 22.575, "p4": 1},
         "s": {"p3001": "heating_circuit", "p3008": "22.5", "p3011": "30", "p3012": "10"}},
        {"n": "Контур отопления", "i": 46, "t": 102, "f": 0, "sev": 1,
         "st": {"p1": 57.5, "p4": 0},
         "s": {"p3001": "heating_circuit", "p3008": "-16777216", "p3022": "-16777216"}},
    ],
    "engs": [{"n": "Клапан 3-ходовой", "i": 71, "t": 308, "f": 0, "sev": 1,
              "st": {"p1": -16777216, "p4": 0}, "s": {}}],
    "alarms": [],
    "heaters": [{"n": "Котел", "i": 45, "t": 303, "f": 2349, "sev": 1,
                 "st": {"p1": -16777216, "p4": -16777216, "p100": 57.5,
                        "p101": 51, "p109": 1.84}, "s": {}}],
    "hModes": [{"i": 1, "n": "Дома"}, {"i": 2, "n": "Лето"}, {"i": 3, "n": "На работе"},
               {"i": 4, "n": "Ночь"}, {"i": 5, "n": "Отпуск"}],
    "scheds": [{"i": 1, "n": "день-ночь"}],
    "deviceSeverity": 1,
}

CLOUD_INFO = {
    "err": 0,
    "data": {
        "heaters": [{"id": 45, "name": "Котел", "disabled": False, "flowTemp": 57.5,
                     "returnTemp": 45.0, "pressure": 1.84, "targetTemp": 51.0,
                     "burnerHeating": True, "burnerWater": False, "modulation": 42}],
        "envs": [
            {"id": 48, "type": "room_temperature", "name": "Температура помещения",
             "value": 22.5, "target": 22.5, "demand": True, "severity": 1, "severityDesc": "ok"},
            {"id": 47, "type": "dhw_temperature", "name": "Контур ГВС",
             "value": 45.0, "target": 50.0, "demand": False, "severity": 1, "severityDesc": "ok"},
            {"id": 46, "type": "circuit_temperature", "name": "Контур отопления",
             "value": 57.5, "target": None, "demand": False, "severity": 1, "severityDesc": "ok"},
            {"id": 49, "type": "floor_temperature", "name": "Теплый пол",
             "value": 28.0, "target": 30.0, "demand": False, "severity": 1, "severityDesc": "ok"},
            {"id": 99, "type": "humidity", "name": "Влажность",
             "value": 45.0, "target": None, "demand": False, "severity": 1, "severityDesc": "ok"},
        ],
        "engs": [{"id": 71, "type": "valve", "name": "Клапан", "turnedOn": False,
                  "severity": 1, "severityDesc": "ok"}],
        "alarms": [], "dataActual": True, "severity": 1, "severityDesc": "ok",
        "weatherTemp": "10.8", "city": "Москва",
    },
}

JSON = {"Content-Type": "text/json"}


def mock_local(aioclient_mock, heater_flags=None):
    obj = LOCAL_OBJ
    if heater_flags is not None:
        import copy
        obj = copy.deepcopy(LOCAL_OBJ)
        obj["heaters"][0]["f"] = heater_flags
    aioclient_mock.post(f"{BASE}/api/login", json={"status": True}, headers=JSON)
    aioclient_mock.post(f"{BASE}/api/getState", json=LOCAL_STATE, headers=JSON)
    aioclient_mock.post(f"{BASE}/api/getObjState", json=obj, headers=JSON)


def local_data(**overrides):
    data = {
        CONF_NAME: f"MyHeat ({HOST})", CONF_USERNAME: "", CONF_API_KEY: "",
        CONF_DEVICE_ID: 0, CONF_DEVICE_KEY: f"local_{SERIAL}",
        CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True, CONF_LOCAL_HOST: HOST,
        CONF_LOCAL_LOGIN: "myheat", CONF_LOCAL_PASSWORD: "myheat",
        CONF_LOCAL_PROTOCOL: "http", CONF_LOCAL_POLL_INTERVAL: 30, CONF_LOCAL_TIMEOUT: 30,
    }
    data.update(overrides)
    return data


def hybrid_data():
    return local_data(
        **{CONF_NAME: "myheat", CONF_USERNAME: "u@x.ru", CONF_API_KEY: "key",
           CONF_DEVICE_ID: 12, CONF_DEVICE_KEY: "12", CONF_LOCAL_ONLY: False}
    )


def states_by_name(hass):
    return {s.attributes.get("friendly_name"): s for s in hass.states.async_all()}


async def setup(hass, data, version=2):
    entry = MockConfigEntry(domain=DOMAIN, version=version, data=data,
                            unique_id=f"myheat_{data[CONF_DEVICE_KEY]}")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_local_only_setup(hass, aioclient_mock, caplog):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    assert entry.state is ConfigEntryState.LOADED

    st = states_by_name(hass)
    names = sorted(st)
    print("\n".join(f"  {n} = {st[n].state}" for n in names))

    assert st["MyHeat (192.168.1.50) Источник данных"].state == "local"
    assert st["MyHeat (192.168.1.50) Текущий режим"].state == "📅 день-ночь"
    assert float(st["MyHeat (192.168.1.50) Баланс SIM"].state) == -1.61
    assert st["MyHeat (192.168.1.50) WiFi SSID"].state == "HomeWiFi"
    assert st["MyHeat (192.168.1.50) Контроллер: интернет"].state == "off"
    assert st["MyHeat (192.168.1.50) Облако доступно"].state == "off"
    ids = [s.entity_id for s in hass.states.async_all()]
    heaters = [i for i in ids if i.startswith("water_heater.")]
    climates = [i for i in ids if i.startswith("climate.")]
    # probes (boiler room, return pipe) must not become water heaters/climates
    assert heaters == ["water_heater.myheat_192_168_1_50_kontur_gvs"], heaters
    assert len(climates) == 2, climates  # room + heating circuit
    # no duplicated unique ids / platform errors / deprecations from us
    assert "does not generate unique IDs" not in caplog.text
    assert "Error adding entit" not in caplog.text
    assert "Traceback" not in caplog.text
    assert "custom integration 'myheat' calls" not in caplog.text


async def test_devices_linked(hass, aioclient_mock):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(reg, entry.entry_id)
    parent = [d for d in devices if (DOMAIN, f"local_{SERIAL}") in d.identifiers]
    assert len(parent) == 1
    children = [d for d in devices if d.id != parent[0].id]
    assert children, "expected per-env/heater child devices"
    unlinked = [d.name for d in children if d.via_device_id != parent[0].id]
    assert not unlinked, f"children not linked to parent: {unlinked}"


async def test_hybrid_cloud_ok_with_local_extras(hass, aioclient_mock, caplog):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    entry = await setup(hass, hybrid_data())
    assert entry.state is ConfigEntryState.LOADED
    assert "custom integration 'myheat' calls" not in caplog.text
    st = states_by_name(hass)
    assert st["myheat Источник данных"].state == "cloud"
    assert st["myheat Облако доступно"].state == "on"
    # local-only sensors are filled from the background local refresh
    assert float(st["myheat Баланс SIM"].state) == -1.61
    assert st["myheat Текущий режим"].state == "📅 день-ночь"


async def test_hybrid_fallback_to_local(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, status=500, text="boom")
    entry = await setup(hass, hybrid_data())
    assert entry.state is ConfigEntryState.LOADED
    st = states_by_name(hass)
    assert st["myheat Источник данных"].state == "local"
    assert st["myheat Облако доступно"].state == "off"


async def test_entity_platforms_split(hass, aioclient_mock):
    """DHW → water_heater, room/circuit/floor → climate, humidity → sensor in %."""
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    ids = [s.entity_id for s in hass.states.async_all()]
    climates = [i for i in ids if i.startswith("climate.")]
    heaters = [i for i in ids if i.startswith("water_heater.")]
    print("climate:", climates)
    print("water_heater:", heaters)
    assert len(heaters) == 1, heaters            # only DHW
    assert len(climates) == 3, climates           # room, circuit, floor
    hum = [s for s in hass.states.async_all()
           if s.attributes.get("device_class") == "humidity"]
    assert len(hum) == 1
    assert hum[0].attributes.get("unit_of_measurement") == "%"
    # DHW and floor keep their standalone temperature sensors
    temp_sensors = [s.attributes.get("friendly_name") for s in hass.states.async_all()
                    if s.entity_id.startswith("sensor.")
                    and s.attributes.get("unit_of_measurement") == "°C"]
    print("temp sensors:", temp_sensors)
    assert any("ГВС" in n for n in temp_sensors)
    assert any("пол" in n.lower() for n in temp_sensors)


async def test_options_flow_changes_settings(hass, aioclient_mock):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form" and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True, CONF_LOCAL_HOST: HOST,
            CONF_LOCAL_LOGIN: "myheat", CONF_LOCAL_PASSWORD: "myheat",
            CONF_LOCAL_PROTOCOL: "http", CONF_LOCAL_POLL_INTERVAL: 45, CONF_LOCAL_TIMEOUT: 20,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data[CONF_LOCAL_POLL_INTERVAL] == 45
    assert entry.data[CONF_LOCAL_TIMEOUT] == 20
    assert entry.state is ConfigEntryState.LOADED


async def test_options_flow_single_reload(hass, aioclient_mock):
    """One options save must reload exactly once (no stacked listeners)."""
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    logins_before = sum(1 for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/login"))
    for interval in (40, 50):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True, CONF_LOCAL_HOST: HOST,
                CONF_LOCAL_LOGIN: "myheat", CONF_LOCAL_PASSWORD: "myheat",
                CONF_LOCAL_PROTOCOL: "http", CONF_LOCAL_POLL_INTERVAL: interval,
                CONF_LOCAL_TIMEOUT: 30,
            },
        )
        await hass.async_block_till_done()
    logins = sum(1 for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/login")) - logins_before
    # per save: 1 probe login in the flow + 1 login on reload = 2
    assert logins == 4, f"expected 4 logins for 2 saves, got {logins}"
    assert entry.state is ConfigEntryState.LOADED


async def test_options_flow_blocks_cloud_for_local_only_entry(hass, aioclient_mock):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: False, CONF_LOCAL_HOST: HOST,
            CONF_LOCAL_LOGIN: "myheat", CONF_LOCAL_PASSWORD: "myheat",
            CONF_LOCAL_PROTOCOL: "http", CONF_LOCAL_POLL_INTERVAL: 30, CONF_LOCAL_TIMEOUT: 30,
        },
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cloud_creds_required"}


async def test_migration_v1_keeps_entity_ids(hass, aioclient_mock):
    mock_local(aioclient_mock)
    data = local_data()
    data.pop(CONF_DEVICE_KEY)
    entry = MockConfigEntry(domain=DOMAIN, version=1, data=data, unique_id="legacy")
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    old = reg.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}active_source",
        config_entry=entry, suggested_object_id="myheat_istochnik_dannykh",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2
    assert entry.data[CONF_DEVICE_KEY] == f"local_{SERIAL}"
    migrated = reg.async_get(old.entity_id)
    assert migrated is not None
    assert migrated.unique_id == f"local_{SERIAL}active_source"
    assert hass.states.get(old.entity_id).state == "local"


async def test_config_flow_bad_cloud_stays_on_user_step(hass, aioclient_mock):
    aioclient_mock.post(CLOUD, json={"err": 1})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "u@x.ru", CONF_API_KEY: "bad",
                    CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: False},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_config_flow_local_only_creates_entry(hass, aioclient_mock):
    mock_local(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "", CONF_API_KEY: "",
                    CONF_LOCAL_ENABLED: True, CONF_LOCAL_ONLY: True},
    )
    assert result["step_id"] == "local"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_LOCAL_HOST: HOST, CONF_LOCAL_LOGIN: "myheat",
                    CONF_LOCAL_PASSWORD: "myheat"},
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_DEVICE_KEY] == f"local_{SERIAL}"
    await hass.async_block_till_done()


ROOM_CLIMATE = "climate.myheat_192_168_1_50_temperatura_pomeshcheniia"


async def test_presets_come_from_controller(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    st = hass.states.get(ROOM_CLIMATE)
    assert st is not None
    assert st.attributes["preset_modes"] == [
        "none", "Дома", "Лето", "На работе", "Ночь", "Отпуск", "📅 день-ночь",
    ]
    # fixture: schedule 1 active, no manual mode
    assert st.attributes["preset_mode"] == "📅 день-ночь"
    # state is filled right at startup, not "off" until the next poll
    assert st.state == "heat"
    assert st.attributes["current_temperature"] == 22.6  # HA rounds to 0.1


def set_obj_calls(aioclient_mock):
    return [c[2] for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/setObjState")]


async def test_set_preset_sends_web_ui_payload(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/setObjState", json={"status": 1}, headers=JSON)
    await setup(hass, local_data())
    for preset in ("Лето", "📅 день-ночь", "none"):
        await hass.services.async_call(
            "climate", "set_preset_mode",
            {"entity_id": ROOM_CLIMATE, "preset_mode": preset}, blocking=True,
        )
    await hass.async_block_till_done()
    assert set_obj_calls(aioclient_mock) == [
        {"action": "setHeatingMode", "mode": 2, "schedule": -1},
        {"action": "setHeatingMode", "mode": -1, "schedule": 1},
        {"action": "setHeatingMode", "mode": -1, "schedule": -1},
    ]


async def test_set_preset_rejected_by_controller(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/setObjState", json={"status": 0}, headers=JSON)
    await setup(hass, local_data())
    import pytest
    with pytest.raises(Exception):
        await hass.services.async_call(
            "climate", "set_preset_mode",
            {"entity_id": ROOM_CLIMATE, "preset_mode": "Лето"}, blocking=True,
        )


async def test_cloud_only_keeps_legacy_presets(hass, aioclient_mock):
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    data = hybrid_data()
    data.update({CONF_LOCAL_ENABLED: False, CONF_LOCAL_ONLY: False})
    await setup(hass, data)
    st = hass.states.get("climate.myheat_temperatura_pomeshcheniia")
    assert st is not None
    assert set(st.attributes["preset_modes"]) == {"away", "eco", "home", "none", "sleep"}


COUNTERS = {
    "sensor.myheat_192_168_1_50_kotel_vremia_raboty_gorelki": ("Время работы горелки", "h"),
    "sensor.myheat_192_168_1_50_kotel_vremia_raboty_na_otoplenie": ("Время работы на отопление", "h"),
    "sensor.myheat_192_168_1_50_kotel_vremia_raboty_na_gvs": ("Время работы на ГВС", "h"),
    "sensor.myheat_192_168_1_50_kotel_rozzhigi_gorelki": ("Розжиги горелки", None),
    "sensor.myheat_192_168_1_50_kotel_vkliucheniia_gvs": ("Включения ГВС", None),
}


async def test_burner_counters_created(hass, aioclient_mock, caplog):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    for eid, (label, unit) in COUNTERS.items():
        st = hass.states.get(eid)
        assert st is not None, eid
        assert st.attributes["friendly_name"] == f"MyHeat (192.168.1.50) Котел {label}"
        assert st.attributes.get("unit_of_measurement") == unit
        assert st.attributes["state_class"] == "total_increasing"
        assert float(st.state) == 0.0
        # attached to the boiler device
        assert dev_reg.async_get(ent_reg.async_get(eid).device_id).name.endswith("Котел")
    assert not [s.entity_id for s in hass.states.async_all() if "mock_title" in s.entity_id]
    assert not [s.entity_id for s in hass.states.async_all() if "plamia" in s.entity_id]
    assert "is using state class" not in caplog.text
    assert entry.runtime_data.burner is not None


async def test_pump_entities(hass, aioclient_mock, monkeypatch):
    from custom_components.myheat import burner as burner_mod
    clock = {"t": 1000.0}
    monkeypatch.setattr(burner_mod.time, "monotonic", lambda: clock["t"])
    mock_local(aioclient_mock, heater_flags=0x0935)        # burning for hot water
    entry = await setup(hass, local_data())
    burner = entry.runtime_data.burner
    pump = hass.states.get("binary_sensor.myheat_192_168_1_50_kotel_nasos")
    overrun_id = "sensor.myheat_192_168_1_50_kotel_vybeg_nasosa"
    assert pump is not None and pump.state == "on"
    assert pump.attributes["friendly_name"] == "MyHeat (192.168.1.50) Котел Насос"
    st = hass.states.get(overrun_id)
    assert st is not None and st.state == "unknown"        # nothing measured yet
    assert st.attributes["unit_of_measurement"] == "s"

    for flags in (0x0924, 0x0924, 0x0920):                 # flame out, pump runs, stops
        aioclient_mock.clear_requests()
        mock_local(aioclient_mock, heater_flags=flags)
        clock["t"] += 15
        await burner.async_refresh()
        await hass.async_block_till_done()
    st = hass.states.get(overrun_id)
    assert float(st.state) == pytest.approx(30.0)
    assert st.attributes["после"] == "ГВС"
    assert st.attributes["точность"] == "±15 с"
    assert hass.states.get("binary_sensor.myheat_192_168_1_50_kotel_nasos").state == "off"


async def test_counter_not_doubled_when_poll_fails(hass, aioclient_mock, monkeypatch):
    from custom_components.myheat import burner as burner_mod
    clock = {"t": 1000.0}
    monkeypatch.setattr(burner_mod.time, "monotonic", lambda: clock["t"])
    mock_local(aioclient_mock)                     # fixture: burning for heating
    entry = await setup(hass, local_data())
    burner = entry.runtime_data.burner
    eid = "sensor.myheat_192_168_1_50_kotel_vremia_raboty_gorelki"

    clock["t"] += 15
    await burner.async_refresh()
    await hass.async_block_till_done()
    after_ok = float(hass.states.get(eid).state)
    assert after_ok == pytest.approx(15 / 3600, abs=1e-5)

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/login", status=500)
    aioclient_mock.post(f"{BASE}/api/getObjState", status=500)
    clock["t"] += 15
    await burner.async_refresh()                   # fails -> listeners get stale data
    await hass.async_block_till_done()
    assert burner.last_update_success is False
    # stays available with the same total: the stale sample is not re-added
    assert float(hass.states.get(eid).state) == after_ok

    aioclient_mock.clear_requests()
    mock_local(aioclient_mock)
    clock["t"] += 15
    await burner.async_refresh()                   # recovered; gap not integrated
    await hass.async_block_till_done()
    assert burner.last_update_success is True
    assert float(hass.states.get(eid).state) == after_ok
    clock["t"] += 15
    await burner.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(eid).state) == pytest.approx(30 / 3600, abs=1e-5)


async def test_hybrid_burner_state_comes_from_controller(hass, aioclient_mock):
    """Cloud says 'heating, no hot water' (minute-old); controller says hot water now."""
    mock_local(aioclient_mock, heater_flags=0x0935)   # recorded: burning for hot water
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)       # cloud: burnerHeating=True, water=False
    await setup(hass, hybrid_data())
    st = states_by_name(hass)
    assert st["myheat Источник данных"].state == "cloud"
    assert st["myheat Котел ГВС"].state == "on"
    assert st["myheat Котел Отопление"].state == "off"
    burner = st["myheat Котел Горелка"]
    assert burner.state == "on"
    assert burner.attributes["источник"] == "контроллер"
    assert burner.attributes["water"] is True and burner.attributes["heating"] is False
    assert burner.attributes["насос"] is True


async def test_hybrid_burner_falls_back_to_cloud(hass, aioclient_mock):
    aioclient_mock.post(f"{BASE}/api/login", status=500)
    aioclient_mock.post(f"{BASE}/api/getState", status=500)
    aioclient_mock.post(f"{BASE}/api/getObjState", status=500)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    st = states_by_name(hass)
    assert st["myheat Котел ГВС"].state == "off"          # cloud value
    assert st["myheat Котел Отопление"].state == "on"     # cloud value
    assert st["myheat Котел Горелка"].attributes["источник"] == "облако"


async def test_no_burner_entities_in_cloud_only(hass, aioclient_mock):
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    data = hybrid_data()
    data.update({CONF_LOCAL_ENABLED: False, CONF_LOCAL_ONLY: False})
    entry = await setup(hass, data)
    assert entry.runtime_data.burner is None
    assert not [s for s in hass.states.async_all() if "vremia_raboty" in s.entity_id]
    assert states_by_name(hass)["myheat Котел Горелка"].attributes["источник"] == "облако"


async def test_local_env_targets_from_settings(hass, aioclient_mock):
    """Local goal = s.p3008 (per the controller UI), not st.p4."""
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    room = hass.states.get("climate.myheat_192_168_1_50_temperatura_pomeshcheniia")
    assert room.attributes["temperature"] == 22.5 and room.state == "heat"
    circuit = hass.states.get("climate.myheat_192_168_1_50_kontur_otopleniia")
    assert circuit.attributes["temperature"] is None and circuit.state == "off"
    dhw = hass.states.get("water_heater.myheat_192_168_1_50_kontur_gvs")
    assert dhw.attributes["temperature"] == 60
    assert dhw.attributes["current_temperature"] is None     # no DHW probe, not 0 °C


async def test_water_heater_shows_setpoint_not_range(hass, aioclient_mock):
    """target_temp_high/low must be empty, otherwise HA shows '7–85 °C'."""
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    dhw = [s for s in hass.states.async_all() if s.entity_id.startswith("water_heater.")][0]
    assert dhw.attributes["target_temp_high"] is None
    assert dhw.attributes["target_temp_low"] is None
    assert dhw.attributes["temperature"] == 50            # cloud target in the fixture
    assert (dhw.attributes["min_temp"], dhw.attributes["max_temp"]) == (7, 85)


async def test_local_heater_return_and_target(hass, aioclient_mock):
    """p101 is the return line (per the controller UI); target is not exposed locally."""
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    assert float(hass.states.get("sensor.myheat_192_168_1_50_kotel_podacha").state) == 57.5
    assert float(hass.states.get("sensor.myheat_192_168_1_50_kotel_obratka").state) == 51.0
    assert hass.states.get("sensor.myheat_192_168_1_50_kotel_tselevaia").state == "unknown"
