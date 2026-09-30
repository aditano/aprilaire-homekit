from aprilaire_homekit.mapping import (
    HK_AUTO,
    HK_COOL,
    HK_HEAT,
    HK_IDLE,
    HK_OFF,
    aprilaire_mode_from_homekit,
    displayed_target_c,
    format_temperature,
    homekit_current_action,
    homekit_target_mode,
    round_half,
    setpoint_write_for_target,
    view_from_data,
)
from pyaprilaire.const import Attribute


def test_mode_round_trip_and_emergency_heat():
    assert homekit_target_mode(1) == HK_OFF
    assert homekit_target_mode(2) == HK_HEAT
    assert homekit_target_mode(4) == HK_HEAT
    assert homekit_target_mode(3) == HK_COOL
    assert homekit_target_mode(5) == HK_AUTO
    assert homekit_target_mode(99) is None
    assert aprilaire_mode_from_homekit(HK_OFF) == 1
    assert aprilaire_mode_from_homekit(HK_HEAT) == 2
    assert aprilaire_mode_from_homekit(HK_COOL) == 3
    assert aprilaire_mode_from_homekit(HK_AUTO) == 5


def test_unknown_homekit_mode_is_rejected():
    try:
        aprilaire_mode_from_homekit(9)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_equipment_action_prefers_heating():
    assert homekit_current_action(2, 0) == 1
    assert homekit_current_action(0, 1) == 2
    assert homekit_current_action(1, 1) == 1
    assert homekit_current_action(0, 0) == HK_IDLE


def test_setpoint_writes_leave_the_other_side_unchanged():
    cool = setpoint_write_for_target(HK_COOL, 21.2)
    assert cool.cool == 21.0
    assert cool.heat == 0
    heat = setpoint_write_for_target(HK_HEAT, 21.3)
    assert heat.heat == 21.5
    assert heat.cool == 0
    assert round_half(21.25) == 21.0 or round_half(21.25) == 21.5


def test_view_uses_controlling_sensor_and_names_8920w():
    view = view_from_data(
        {
            Attribute.MODE: 4,
            Attribute.HEATING_EQUIPMENT_STATUS: 0,
            Attribute.COOLING_EQUIPMENT_STATUS: 0,
            Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_STATUS: 0,
            Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_VALUE: 22,
            Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_STATUS: 1,
            Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_VALUE: 40,
            Attribute.HEAT_SETPOINT: 21.5,
            Attribute.COOL_SETPOINT: 24,
            Attribute.MODEL_NUMBER: 6,
            Attribute.MAC_ADDRESS: "2:11:22:33:44:55",
            Attribute.NAME: "Hall",
        },
        connected=True,
    )
    assert view.model == "8920W"
    assert view.mac == "02:11:22:33:44:55"
    assert view.mode_label == "Emergency heat"
    assert view.hk_mode == HK_HEAT
    assert view.current_c == 22
    assert view.humidity is None
    assert view.name == "Hall"
    assert displayed_target_c(view.hk_mode, view.heat_c, view.cool_c) == 21.5
    assert format_temperature(22, "F") == "72°F"
    assert format_temperature(22, "C") == "22°C"


def test_missing_data_does_not_invent_a_mode():
    view = view_from_data({}, connected=False, last_error="down")
    assert view.hk_mode is None
    assert view.current_c is None
    assert view.model is None
    assert view.last_error == "down"
    assert format_temperature(None, "F") is None
