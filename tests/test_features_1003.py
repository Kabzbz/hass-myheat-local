"""v10.03: alarms, gas estimate, controller limits, fewer requests, diagnostics, reauth."""

from pathlib import Path
import shutil

import pytest
from homeassistant import config_entries
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.myheat.const import CONF_GAS_RATE
from custom_components.myheat.diagnostics import async_get_config_entry_diagnostics
from tests.test_myheat import (
    CLOUD,
    CLOUD_INFO,
    LOCAL_STATE,
    hybrid_data,
    local_data,
    mock_local,
    setup,
)

BOILER = "myheat_192_168_1_50_kotel"
BLUEPRINT = Path(__file__).parent.parent / "blueprints" / "automation" / "myheat" / "alerts.yaml"


def calls(aioclient_mock, path):
    return sum(1 for c in aioclient_mock.mock_calls if str(c[1]).endswith(path))


# --- 1. alarms --------------------------------------------------------------

async def test_alarm_sensors(hass, aioclient_mock):
    mock_local(aioclient_mock, heater_flags=0x092D | 0x0400)   # heating + low pressure
    await setup(hass, local_data())
    assert hass.states.get(f"binary_sensor.{BOILER}_nizkoe_davlenie").state == "on"
    assert hass.states.get(f"binary_sensor.{BOILER}_oshibka_kotla").state == "off"
    assert hass.states.get(f"binary_sensor.{BOILER}_sviaz_s_kotlom").state == "on"
    st = hass.states.get(f"binary_sensor.{BOILER}_nizkoe_davlenie")
    assert st.attributes["device_class"] == "problem"


async def test_link_lost(hass, aioclient_mock):
    mock_local(aioclient_mock, heater_flags=0x0900)             # 0x20 cleared
    await setup(hass, local_data())
    assert hass.states.get(f"binary_sensor.{BOILER}_sviaz_s_kotlom").state == "off"


async def test_alerts_blueprint(hass):
    target = Path(hass.config.path("blueprints/automation/myheat/alerts.yaml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target)
    notify = async_mock_service(hass, "test", "notify")
    for eid, state in (("binary_sensor.f", "off"), ("binary_sensor.p", "off"),
                       ("binary_sensor.l", "on"), ("sensor.src", "cloud"), ("sensor.sim", "10")):
        hass.states.async_set(eid, state)
    assert await async_setup_component(hass, "persistent_notification", {})
    assert await async_setup_component(hass, "automation", {"automation": [{
        "alias": "MyHeat тревоги",
        "use_blueprint": {"path": "myheat/alerts.yaml", "input": {
            "fault_sensor": "binary_sensor.f", "pressure_sensor": "binary_sensor.p",
            "link_sensor": "binary_sensor.l", "source_sensor": "sensor.src",
            "sim_sensor": "sensor.sim", "sim_threshold": 0,
            "notify_action": [{"action": "test.notify", "data": {"message": "{{ message }}"}}],
        }},
    }]})
    await hass.async_block_till_done()
    automation = hass.states.get("automation.myheat_trevogi")
    assert automation is not None and automation.state == "on"   # blueprint valid

    hass.states.async_set("binary_sensor.f", "on")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.p", "on")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.sim", "-1.61")
    await hass.async_block_till_done()
    messages = [c.data["message"] for c in notify]
    assert messages == [
        "Котёл сообщает об ошибке.",
        "Низкое давление в контуре отопления.",
        "Баланс SIM MyHeat: -1.61 ₽ — пора пополнить.",
    ]


# --- 2. gas -----------------------------------------------------------------

async def test_gas_estimate(hass, aioclient_mock, monkeypatch):
    from custom_components.myheat import burner as burner_mod
    clock = {"t": 1000.0}
    monkeypatch.setattr(burner_mod.time, "monotonic", lambda: clock["t"])
    mock_local(aioclient_mock)                     # burning for heating
    entry = await setup(hass, local_data(**{CONF_GAS_RATE: 2.4}))
    total = f"sensor.{BOILER}_gaz_otsenka"
    st = hass.states.get(total)
    assert st is not None
    assert st.attributes["device_class"] == "gas"
    assert st.attributes["unit_of_measurement"] == "m³"
    assert st.attributes["state_class"] == "total_increasing"
    clock["t"] += 15
    await entry.runtime_data.burner.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(total).state) == pytest.approx(15 / 3600 * 2.4, abs=1e-4)
    assert float(hass.states.get(f"sensor.{BOILER}_gaz_na_otoplenie_otsenka").state) == pytest.approx(0.01, abs=1e-4)
    assert float(hass.states.get(f"sensor.{BOILER}_gaz_na_gvs_otsenka").state) == 0


async def test_no_gas_without_rate(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    assert not [s for s in hass.states.async_all() if "_gaz_" in s.entity_id]


# --- 3. controller limits ---------------------------------------------------

async def test_controller_goal_limits_local(hass, aioclient_mock):
    mock_local(aioclient_mock)
    await setup(hass, local_data())
    room = hass.states.get("climate.myheat_192_168_1_50_temperatura_pomeshcheniia")
    assert (room.attributes["min_temp"], room.attributes["max_temp"]) == (10, 30)
    dhw = hass.states.get("water_heater.myheat_192_168_1_50_kontur_gvs")
    assert (dhw.attributes["min_temp"], dhw.attributes["max_temp"]) == (40, 65)
    # no limits in the controller settings -> type-based rule stays (circuit 20..85)
    circuit = hass.states.get("climate.myheat_192_168_1_50_kontur_otopleniia")
    assert (circuit.attributes["min_temp"], circuit.attributes["max_temp"]) == (20, 85)


async def test_controller_goal_limits_hybrid(hass, aioclient_mock):
    """Cloud is the source, limits still come from the cached controller data."""
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await setup(hass, hybrid_data())
    room = hass.states.get("climate.myheat_temperatura_pomeshcheniia")
    assert (room.attributes["min_temp"], room.attributes["max_temp"]) == (10, 30)
    dhw = hass.states.get("water_heater.myheat_kontur_gvs")
    assert (dhw.attributes["min_temp"], dhw.attributes["max_temp"]) == (40, 65)


async def test_limits_fall_back_without_local(hass, aioclient_mock):
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    data = hybrid_data()
    data.update({"local_enabled": False, "local_only": False})
    await setup(hass, data)
    room = hass.states.get("climate.myheat_temperatura_pomeshcheniia")
    assert (room.attributes["min_temp"], room.attributes["max_temp"]) == (5, 30)
    dhw = hass.states.get("water_heater.myheat_kontur_gvs")
    assert (dhw.attributes["min_temp"], dhw.attributes["max_temp"]) == (7, 85)


# --- 5. fewer requests ------------------------------------------------------

async def test_main_poll_reuses_burner_snapshot(hass, aioclient_mock):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    coordinator = entry.runtime_data
    obj_before, state_before = calls(aioclient_mock, "/api/getObjState"), calls(aioclient_mock, "/api/getState")
    await coordinator.async_refresh()
    assert calls(aioclient_mock, "/api/getObjState") == obj_before       # reused
    assert calls(aioclient_mock, "/api/getState") == state_before + 1
    # after a write the controller is read directly once
    coordinator.mark_local_stale()
    await coordinator.async_refresh()
    assert calls(aioclient_mock, "/api/getObjState") == obj_before + 1


# --- 6. diagnostics & reauth -------------------------------------------------

async def test_diagnostics_redacted(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    entry = await setup(hass, hybrid_data())
    diag = await async_get_config_entry_diagnostics(hass, entry)
    data = diag["entry"]["data"]
    for key in ("api_key", "username", "local_password", "local_login", "device_key"):
        assert data[key] == "**REDACTED**", key
    text = str(diag)
    assert LOCAL_STATE["serial"] not in text
    assert "HomeWiFi" not in text
    assert diag["active_source"] == "cloud"
    assert diag["burner_poller"]["last_update_success"] is True
    assert diag["integration_version"] == "10.03"


def reauth_flows(hass):
    return [f for f in hass.config_entries.flow.async_progress()
            if f["context"].get("source") == config_entries.SOURCE_REAUTH]


async def test_reauth_after_repeated_refusals(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json={"err": 3})
    entry = await setup(hass, hybrid_data())
    for _ in range(12):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(reauth_flows(hass)) == 1
    # local fallback keeps working meanwhile
    assert entry.runtime_data.active_source == "local"


async def test_no_reauth_on_network_errors(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, status=502, text="bad gateway")
    entry = await setup(hass, hybrid_data())
    for _ in range(12):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert reauth_flows(hass) == []


async def test_reauth_flow_updates_key(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json={"err": 0, "data": {"devices": [
        {"id": 12, "name": "Дом", "city": "", "severity": 1, "severityDesc": ""}]}})
    entry = await setup(hass, hybrid_data())
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "u@x.ru", "api_key": "new-key"})
    assert result["type"] == "abort" and result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "new-key"


async def test_reauth_flow_wrong_device(hass, aioclient_mock):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json={"err": 0, "data": {"devices": [
        {"id": 99, "name": "Чужой", "city": "", "severity": 1, "severityDesc": ""}]}})
    entry = await setup(hass, hybrid_data())
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "u@x.ru", "api_key": "other"})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "device_not_found"}
