"""Map AprilAire automation values onto HomeKit thermostat characteristics.

Temperatures on the wire are a 6-bit magnitude plus a half-degree flag and a
sign bit (see pyaprilaire's temperature codec). That encoding cannot hold a
typical Fahrenheit room temperature, and Home Assistant treats the decoded
number as Celsius. HomeKit also wants Celsius on the wire. Display units are
presentation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyaprilaire.const import MODELS, Attribute

APRILAIRE_OFF = 1
APRILAIRE_HEAT = 2
APRILAIRE_COOL = 3
APRILAIRE_EMERGENCY_HEAT = 4
APRILAIRE_AUTO = 5

HK_OFF = 0
HK_HEAT = 1
HK_COOL = 2
HK_AUTO = 3

HK_IDLE = 0
HK_CURRENT_HEAT = 1
HK_CURRENT_COOL = 2

# A write of 0 tells the thermostat to leave that setpoint alone.
UNCHANGED_SETPOINT = 0.0

_HK_MODE_LABELS = {
    HK_OFF: "Off",
    HK_HEAT: "Heat",
    HK_COOL: "Cool",
    HK_AUTO: "Auto",
}

_ACTION_LABELS = {
    HK_IDLE: "Idle",
    HK_CURRENT_HEAT: "Heating",
    HK_CURRENT_COOL: "Cooling",
}


@dataclass(frozen=True)
class ThermostatView:
    """Snapshot safe to show in the window and to push into HomeKit."""

    connected: bool
    raw_mode: int | None
    hk_mode: int | None
    action: int
    current_c: float | None
    heat_c: float | None
    cool_c: float | None
    humidity: int | None
    model: str | None
    model_id: int | None
    name: str | None
    mac: str | None
    last_error: str | None = None

    @property
    def mode_label(self) -> str | None:
        if self.raw_mode == APRILAIRE_EMERGENCY_HEAT:
            return "Emergency heat"
        if self.hk_mode is None:
            return None
        return _HK_MODE_LABELS.get(self.hk_mode)

    @property
    def action_label(self) -> str:
        return _ACTION_LABELS.get(self.action, "Idle")


@dataclass(frozen=True)
class SetpointWrite:
    """Heat and cool values for one control write. Zero means 'leave it'."""

    heat: float
    cool: float


def round_half(celsius: float) -> float:
    """The automation protocol steps in half degrees."""
    return round(float(celsius) * 2.0) / 2.0


def homekit_target_mode(raw_mode: int | None) -> int | None:
    match raw_mode:
        case 1:
            return HK_OFF
        case 2 | 4:
            return HK_HEAT
        case 3:
            return HK_COOL
        case 5:
            return HK_AUTO
        case _:
            return None


def aprilaire_mode_from_homekit(mode: int) -> int:
    match mode:
        case 0:
            return APRILAIRE_OFF
        case 1:
            return APRILAIRE_HEAT
        case 2:
            return APRILAIRE_COOL
        case 3:
            return APRILAIRE_AUTO
        case _:
            raise ValueError(f"Unsupported HomeKit mode {mode}")


def homekit_current_action(heating: int | None, cooling: int | None) -> int:
    """Non-zero equipment status, including 'wait', counts as active.

    That matches the Home Assistant climate entity. Heating wins if both
    sides report activity.
    """
    if heating:
        return HK_CURRENT_HEAT
    if cooling:
        return HK_CURRENT_COOL
    return HK_IDLE


def displayed_target_c(
    hk_mode: int | None, heat_c: float | None, cool_c: float | None
) -> float | None:
    if hk_mode == HK_COOL:
        return cool_c
    if heat_c is not None:
        return heat_c
    return cool_c


def setpoint_write_for_target(hk_mode: int | None, celsius: float) -> SetpointWrite:
    value = round_half(celsius)
    if hk_mode == HK_COOL:
        return SetpointWrite(heat=UNCHANGED_SETPOINT, cool=value)
    return SetpointWrite(heat=value, cool=UNCHANGED_SETPOINT)


def setpoint_write_heat(celsius: float) -> SetpointWrite:
    return SetpointWrite(heat=round_half(celsius), cool=UNCHANGED_SETPOINT)


def setpoint_write_cool(celsius: float) -> SetpointWrite:
    return SetpointWrite(heat=UNCHANGED_SETPOINT, cool=round_half(celsius))


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def format_mac(raw: str) -> str:
    parts = [part.strip() for part in raw.split(":") if part.strip()]
    return ":".join(part.zfill(2).upper() for part in parts)


def model_name(model_id: int | None) -> str | None:
    if model_id is None:
        return None
    return MODELS.get(int(model_id), f"Model {model_id}")


def to_display(celsius: float | None, unit: str) -> float | None:
    if celsius is None:
        return None
    if unit.upper() == "F":
        # Half-degree Celsius steps do not land on every Fahrenheit integer.
        # The window shows the nearest degree, which is what a thermostat does.
        fahrenheit = celsius * 9.0 / 5.0 + 32.0
        if fahrenheit >= 0:
            return float(int(fahrenheit + 0.5))
        return float(int(fahrenheit - 0.5))
    return round(celsius, 1)


def format_temperature(celsius: float | None, unit: str) -> str | None:
    value = to_display(celsius, unit)
    if value is None:
        return None
    suffix = "F" if unit.upper() == "F" else "C"
    if suffix == "F" or abs(value - round(value)) < 0.05:
        return f"{round(value):.0f}°{suffix}"
    return f"{value:.1f}°{suffix}"


def _reading(data: dict[str, Any], status_key: str, value_key: str) -> Any:
    if status_key in data and data.get(status_key) not in (0, None):
        return None
    return data.get(value_key)


def view_from_data(
    data: dict[str, Any],
    *,
    connected: bool,
    last_error: str | None = None,
) -> ThermostatView:
    raw_mode = data.get(Attribute.MODE)
    if raw_mode is not None:
        raw_mode = int(raw_mode)
    hk_mode = homekit_target_mode(raw_mode)
    current = _reading(
        data,
        Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_STATUS,
        Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_VALUE,
    )
    humidity = _reading(
        data,
        Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_STATUS,
        Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_VALUE,
    )
    model_id = data.get(Attribute.MODEL_NUMBER)
    if model_id is not None:
        model_id = int(model_id)
    mac = data.get(Attribute.MAC_ADDRESS)
    name = data.get(Attribute.NAME)
    if isinstance(name, str):
        name = name.strip() or None
    return ThermostatView(
        connected=connected,
        raw_mode=raw_mode,
        hk_mode=hk_mode,
        action=homekit_current_action(
            data.get(Attribute.HEATING_EQUIPMENT_STATUS),
            data.get(Attribute.COOLING_EQUIPMENT_STATUS),
        ),
        current_c=None if current is None else float(current),
        heat_c=_optional_float(data.get(Attribute.HEAT_SETPOINT)),
        cool_c=_optional_float(data.get(Attribute.COOL_SETPOINT)),
        humidity=None if humidity is None else int(humidity),
        model=model_name(model_id),
        model_id=model_id,
        name=name,
        mac=format_mac(mac) if isinstance(mac, str) and mac else None,
        last_error=last_error,
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
