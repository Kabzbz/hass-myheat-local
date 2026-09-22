"""v10.05: controller data when the cloud's is stale, quiet single controller misses."""

import copy
import logging
from types import SimpleNamespace

from custom_components.myheat import burner as burner_mod
from custom_components.myheat.const import CONF_LOCAL_ENABLED, MISSES_BEFORE_ERROR
from tests.test_features_1003 import calls
from tests.test_features_1004 import OURS, ours
from tests.test_myheat import (
    BASE,
    CLOUD,
    CLOUD_INFO,
    hybrid_data,
    local_data,
    mock_local,
    setup,
    states_by_name,
)

STALE = "MyHeat cloud has no fresh data from the controller (dataActual=false)"


def stale_cloud_info():
    info = copy.deepcopy(CLOUD_INFO)
    info["data"]["dataActual"] = False
    return info


def messages(caplog, level=None):
    return [r.getMessage() for r in ours(caplog) if level is None or r.levelno == level]


# --- 1. dataActual=false: hybrid takes the controller's data -------------------

async def test_hybrid_stale_cloud_uses_controller(hass, aioclient_mock, caplog):
    caplog.set_level(logging.DEBUG, logger=OURS)
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=stale_cloud_info())
    entry = await setup(hass, hybrid_data())
    coordinator = entry.runtime_data
    await coordinator.async_refresh()

    st = states_by_name(hass)
    assert st["myheat Источник данных"].state == "local"
    assert st["myheat Облако доступно"].state == "off"
    assert st["myheat Подключение"].state == "on"          # fresh from the controller
    assert float(st["myheat Котел Обратка"].state) == 51   # local value, cloud says 45
    assert messages(caplog, logging.INFO).count(
        "Switched to LOCAL source (cloud: no fresh data from the controller)"
    ) == 1
    # handled by the controller: no warning, and no per-entity spam
    assert STALE not in messages(caplog)
    assert not [m for m in messages(caplog) if "not actual" in m]

    aioclient_mock.clear_requests()
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.active_source == "cloud"
    assert "Switched back to CLOUD source" in messages(caplog, logging.INFO)
    assert float(states_by_name(hass)["myheat Котел Обратка"].state) == 45


async def test_stale_cloud_only_warns_once(hass, aioclient_mock, caplog):
    caplog.set_level(logging.DEBUG, logger=OURS)
    aioclient_mock.post(CLOUD, json=stale_cloud_info())
    data = hybrid_data()
    data.update({CONF_LOCAL_ENABLED: False})
    entry = await setup(hass, data)
    coordinator = entry.runtime_data
    for _ in range(3):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    st = states_by_name(hass)
    assert st["myheat Источник данных"].state == "cloud"
    assert st["myheat Котел Обратка"].state == "unknown"   # stale values are not shown
    assert messages(caplog, logging.WARNING) == [STALE]
    assert not [m for m in messages(caplog) if "not actual" in m]

    aioclient_mock.clear_requests()
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "MyHeat cloud has fresh controller data again" in messages(caplog, logging.INFO)
    assert float(states_by_name(hass)["myheat Котел Обратка"].state) == 45


async def test_stale_cloud_and_silent_controller(hass, aioclient_mock, caplog):
    aioclient_mock.post(f"{BASE}/api/login", status=500)
    aioclient_mock.post(CLOUD, json=stale_cloud_info())
    entry = await setup(hass, hybrid_data())
    coordinator = entry.runtime_data
    assert coordinator.active_source == "cloud"           # old data beats none
    assert coordinator.last_update_success is True
    assert messages(caplog, logging.WARNING) == [STALE]

    before = calls(aioclient_mock, "/api/login")
    await coordinator.async_refresh()
    # the controller is asked once per update, not again for the local cache
    assert calls(aioclient_mock, "/api/login") - before == 1
    assert messages(caplog, logging.WARNING) == [STALE]


# --- 2. a missed burner poll is not an error -----------------------------------

async def test_single_burner_miss_is_quiet(hass, aioclient_mock, caplog, monkeypatch):
    # burner's clock only: patching time.monotonic itself also moves HA's
    # event loop and fires the main poll in between
    clock = {"t": 1000.0}
    monkeypatch.setattr(burner_mod, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    mock_local(aioclient_mock, heater_flags=0x0935)        # burning for hot water
    entry = await setup(hass, local_data())
    burner = entry.runtime_data.burner
    pump = "binary_sensor.myheat_192_168_1_50_kotel_nasos"
    runtime = "sensor.myheat_192_168_1_50_kotel_vremia_raboty_gorelki"
    clock["t"] += 15
    await burner.async_refresh()
    await hass.async_block_till_done()
    total = hass.states.get(runtime).state

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/login", status=500)
    aioclient_mock.post(f"{BASE}/api/getObjState", status=500)
    for _ in range(burner_mod.MISSES_BEFORE_ERROR - 1):
        clock["t"] += 15
        await burner.async_refresh()
        await hass.async_block_till_done()
        assert burner.last_update_success is True
        assert hass.states.get(pump).state == "on"          # last known, not unavailable
        assert hass.states.get(runtime).state == total
    assert not [r for r in ours(caplog) if r.levelno >= logging.WARNING]

    clock["t"] += 15
    await burner.async_refresh()                           # one miss too many
    await hass.async_block_till_done()
    assert burner.last_update_success is False
    assert hass.states.get(pump).state == "unavailable"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "burner data" in errors[0]

    aioclient_mock.clear_requests()
    mock_local(aioclient_mock, heater_flags=0x0935)
    clock["t"] += 15
    await burner.async_refresh()
    await hass.async_block_till_done()
    assert burner.last_update_success is True
    assert hass.states.get(pump).state == "on"
    assert len(messages(caplog, logging.ERROR)) == 1

    # a new streak starts from zero
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/api/login", status=500)
    aioclient_mock.post(f"{BASE}/api/getObjState", status=500)
    clock["t"] += 15
    await burner.async_refresh()
    assert burner.last_update_success is True


# --- 3. a missed main poll is not an error either -------------------------------

def controller_down(aioclient_mock):
    aioclient_mock.clear_requests()
    for path in ("login", "getState", "getObjState"):
        aioclient_mock.post(f"{BASE}/api/{path}", status=500)


async def test_local_only_single_miss_is_quiet(hass, aioclient_mock, caplog):
    mock_local(aioclient_mock)
    entry = await setup(hass, local_data())
    coordinator = entry.runtime_data
    room = next(
        s.entity_id for s in hass.states.async_all("sensor")
        if s.attributes.get("тип_среды") == "room_temperature"
    )
    value = hass.states.get(room).state
    assert value not in ("unknown", "unavailable")

    controller_down(aioclient_mock)
    for _ in range(MISSES_BEFORE_ERROR - 1):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.last_update_success is True
        assert coordinator.active_source == "local"
        assert hass.states.get(room).state == value          # last value, not unavailable
    assert not [r for r in ours(caplog) if r.levelno >= logging.WARNING]

    await coordinator.async_refresh()                        # one miss too many
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert coordinator.active_source == "offline"
    assert hass.states.get(room).state == "unavailable"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "local: LocalApiError" in errors[0]

    aioclient_mock.clear_requests()
    mock_local(aioclient_mock)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success is True
    assert hass.states.get(room).state == value

    controller_down(aioclient_mock)                          # a new streak starts from zero
    await coordinator.async_refresh()
    assert coordinator.last_update_success is True


async def test_hybrid_on_controller_single_miss_is_quiet(hass, aioclient_mock, caplog):
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, exc=TimeoutError())
    entry = await setup(hass, hybrid_data())
    coordinator = entry.runtime_data
    assert coordinator.active_source == "local"

    controller_down(aioclient_mock)
    aioclient_mock.post(CLOUD, exc=TimeoutError())
    for _ in range(MISSES_BEFORE_ERROR - 1):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert coordinator.active_source == "local"
    assert not [r for r in ours(caplog) if r.levelno >= logging.ERROR]

    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.active_source == "offline"
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1 and "both cloud and local failed" in errors[0]


async def test_miss_on_cloud_source_is_not_hidden(hass, aioclient_mock):
    """Previous data came from the cloud: nothing local to keep."""
    mock_local(aioclient_mock)
    aioclient_mock.post(CLOUD, json=CLOUD_INFO)
    entry = await setup(hass, hybrid_data())
    coordinator = entry.runtime_data
    assert coordinator.active_source == "cloud"

    controller_down(aioclient_mock)
    aioclient_mock.post(CLOUD, exc=TimeoutError())
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    assert coordinator.active_source == "offline"
