"""Burner run-time accounting: trapezoid integration, ignitions, gaps."""

import pytest

from custom_components.myheat import burner as burner_mod
from custom_components.myheat.burner import (
    MhBurnerCoordinator,
    decode_heater_flags,
)
from custom_components.myheat.local_api import LocalApiError, translate_local_to_cloud
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Flag values recorded on the real controller during the hot-water test.
IDLE = 0x0920            # standing
DHW_BURNING = 0x0935     # tap open: flame + pump + hot water
PUMP_OVERRUN = 0x0924    # tap closed, flame out, pump still running ~20 s
CH_BURNING = 0x092D      # heating: flame + pump + heating

OFF = IDLE
ON = DHW_BURNING


def test_real_flag_values_decode():
    assert decode_heater_flags(IDLE) == {
        "link": True, "fault": False, "flame": False, "pump": False, "ch": False, "dhw": False,
    }
    assert decode_heater_flags(DHW_BURNING) == {
        "link": True, "fault": False, "flame": True, "pump": True, "ch": False, "dhw": True,
    }
    assert decode_heater_flags(PUMP_OVERRUN)["flame"] is False
    assert decode_heater_flags(PUMP_OVERRUN)["pump"] is True
    ch = decode_heater_flags(CH_BURNING)
    assert ch["flame"] and ch["ch"] and not ch["dhw"]


def test_translator_burner_heating_vs_water():
    def heater(flags):
        obj = {"heaters": [{"i": 45, "n": "Котел", "f": flags, "st": {}}]}
        return translate_local_to_cloud(state={}, obj_state=obj)["heaters"][0]

    assert (heater(DHW_BURNING)["burnerHeating"], heater(DHW_BURNING)["burnerWater"]) == (False, True)
    assert (heater(CH_BURNING)["burnerHeating"], heater(CH_BURNING)["burnerWater"]) == (True, False)
    assert (heater(PUMP_OVERRUN)["burnerHeating"], heater(PUMP_OVERRUN)["burnerWater"]) == (False, False)
    assert (heater(IDLE)["burnerHeating"], heater(IDLE)["burnerWater"]) == (False, False)


class FakeLocal:
    def __init__(self, script):
        self.script = list(script)

    async def async_get_obj_state(self):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"heaters": [{"i": 45, "f": item}]}


async def run(hass, monkeypatch, samples, interval=15):
    """samples: list of (time_s, flags or Exception). Returns list of data dicts."""
    entry = MockConfigEntry(domain="myheat", data={})
    entry.add_to_hass(hass)
    local = FakeLocal([f for _, f in samples])
    coord = MhBurnerCoordinator(hass, entry, local, interval)
    out = []
    for t, _ in samples:
        monkeypatch.setattr(burner_mod.time, "monotonic", lambda t=t: float(t))
        try:
            out.append(await coord._async_update_data())
        except UpdateFailed:
            out.append(None)
    return out


async def test_trapezoid_and_ignition(hass, monkeypatch):
    data = await run(hass, monkeypatch, [(0, OFF), (15, ON), (30, ON), (45, OFF)])
    assert [d["flame"] for d in data] == [False, True, True, False]
    assert sum(d["on_seconds"] for d in data) == pytest.approx(30.0)
    assert sum(d["ignitions"] for d in data) == 1


async def test_first_sample_on_is_not_an_ignition(hass, monkeypatch):
    data = await run(hass, monkeypatch, [(0, ON), (15, ON)])
    assert sum(d["ignitions"] for d in data) == 0
    assert sum(d["on_seconds"] for d in data) == pytest.approx(15.0)


async def test_gap_is_not_integrated(hass, monkeypatch):
    data = await run(
        hass, monkeypatch,
        [(0, OFF), (15, ON), (30, LocalApiError("timeout")), (300, ON), (315, OFF)],
    )
    assert data[2] is None
    # 15..30 lost (gap), 300 has no previous timestamp, 300..315 = 7.5
    assert sum(d["on_seconds"] for d in data if d) == pytest.approx(7.5 + 7.5)
    assert sum(d["ignitions"] for d in data if d) == 1


async def test_long_step_without_error_is_not_integrated(hass, monkeypatch):
    data = await run(hass, monkeypatch, [(0, ON), (15, ON), (200, ON)])
    assert sum(d["on_seconds"] for d in data) == pytest.approx(15.0)


async def test_replay_real_hot_water_test(hass, monkeypatch):
    """The real 13:03:49..13:06:34 recording, sampled every 5 s."""
    seq = []
    t = 0
    for _ in range(11):          # 13:03:49 .. 13:04:39 idle
        seq.append((t, IDLE)); t += 5
    for _ in range(18):          # 13:04:44 .. 13:06:09 burning for hot water
        seq.append((t, DHW_BURNING)); t += 5
    for _ in range(4):           # 13:06:14 .. 13:06:29 pump overrun
        seq.append((t, PUMP_OVERRUN)); t += 5
    seq.append((t, IDLE))        # 13:06:34
    data = await run(hass, monkeypatch, seq, interval=5)
    assert sum(d["ignitions"] for d in data) == 1
    burned = sum(d["on_seconds"] for d in data)
    # 18 "on" samples 5 s apart = 85 s between first and last on-sample,
    # plus half a step at each edge = 90 s. True burn: 85..95 s.
    assert burned == pytest.approx(90.0)
    # all of it was hot water
    assert sum(d["dhw_seconds"] for d in data) == pytest.approx(90.0)
    assert sum(d["ch_seconds"] for d in data) == 0
    assert sum(d["dhw_starts"] for d in data) == 1
    # the pump overrun must not be counted as burner time
    assert all(d["on_seconds"] == 0 for d in data[-4:])


def recorded_tap_test(step=5):
    """The real 13:03:49..13:06:34 recording + the pump-only run seen after it."""
    seq, t = [], 0
    for flags, count in ((IDLE, 11), (DHW_BURNING, 18), (PUMP_OVERRUN, 4), (IDLE, 1)):
        for _ in range(count):
            seq.append((t, flags))
            t += step
    return seq


async def test_pump_overrun_from_recording(hass, monkeypatch):
    data = await run(hass, monkeypatch, recorded_tap_test(), interval=5)
    rec = data[-1]["last_overrun"]
    # flame out between 13:06:09 and :14, pump stopped between :29 and :34
    assert rec["seconds"] == pytest.approx(20.0)
    assert rec["after"] == "ГВС"


async def test_pump_running_alone_is_not_an_overrun(hass, monkeypatch):
    seq = [(0, IDLE), (5, PUMP_OVERRUN), (10, PUMP_OVERRUN), (15, IDLE)]  # 13:09:59 case
    data = await run(hass, monkeypatch, seq, interval=5)
    assert data[-1]["last_overrun"] is None
    assert [d["pump"] for d in data] == [False, True, True, False]


async def test_reignition_interrupts_overrun(hass, monkeypatch):
    seq = [(0, CH_BURNING), (15, PUMP_OVERRUN), (30, CH_BURNING), (45, PUMP_OVERRUN), (60, IDLE)]
    data = await run(hass, monkeypatch, seq)
    rec = data[-1]["last_overrun"]
    assert rec["id"] == 1                       # only the last, complete overrun
    assert rec["seconds"] == pytest.approx(15.0)
    assert rec["after"] == "отопление"


async def test_pump_stops_with_flame_is_zero_overrun(hass, monkeypatch):
    data = await run(hass, monkeypatch, [(0, DHW_BURNING), (15, IDLE)])
    assert data[-1]["last_overrun"]["seconds"] == 0.0


async def test_gap_cancels_overrun(hass, monkeypatch):
    seq = [(0, DHW_BURNING), (15, PUMP_OVERRUN), (30, LocalApiError("x")), (45, IDLE)]
    data = await run(hass, monkeypatch, seq)
    assert data[-1]["last_overrun"] is None


async def test_heating_then_hot_water_without_flame_out(hass, monkeypatch):
    """Tap opened while heating: combi switches to hot water, flame stays lit."""
    seq = [(0, IDLE), (15, CH_BURNING), (30, CH_BURNING),
           (45, DHW_BURNING), (60, DHW_BURNING), (75, IDLE)]
    data = await run(hass, monkeypatch, seq)
    assert sum(d["ignitions"] for d in data) == 1       # one real ignition
    assert sum(d["dhw_starts"] for d in data) == 1      # one hot-water session
    assert sum(d["ch_seconds"] for d in data) == pytest.approx(30.0)
    assert sum(d["dhw_seconds"] for d in data) == pytest.approx(30.0)
    assert sum(d["on_seconds"] for d in data) == pytest.approx(60.0)


async def test_samples_are_numbered(hass, monkeypatch):
    data = await run(hass, monkeypatch, [(0, IDLE), (15, IDLE), (30, LocalApiError("x")), (45, IDLE)])
    assert [d["seq"] for d in data if d] == [1, 2, 3]


async def test_short_cycles_counted(hass, monkeypatch):
    seq = [(0, OFF), (15, ON), (30, OFF), (45, ON), (60, OFF), (75, ON), (90, OFF)]
    data = await run(hass, monkeypatch, seq)
    assert sum(d["ignitions"] for d in data) == 3
    assert sum(d["on_seconds"] for d in data) == pytest.approx(3 * 15.0)
