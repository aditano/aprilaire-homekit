"""HomeKit thermostat accessory backed by one AprilAire session."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_THERMOSTAT

from aprilaire_homekit.mapping import ThermostatView, clamp, displayed_target_c
from aprilaire_homekit.session import ThermostatLink

_LOG = logging.getLogger(__name__)


class AprilaireThermostat(Accessory):
    """A single thermostat. HomeKit is the only remote control surface."""

    category = CATEGORY_THERMOSTAT

    def __init__(
        self,
        driver: AccessoryDriver,
        display_name: str,
        host: str,
        port: int,
        display_unit: str,
        on_view,
    ) -> None:
        super().__init__(driver, display_name)
        self._host = host
        self._port = port
        self._display_unit = display_unit
        self._on_view = on_view
        self._stop = asyncio.Event()
        self._stopped = asyncio.Event()
        self._link: ThermostatLink | None = None
        self._identity_sent = False

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="AprilAire")
        info.configure_char("Model", value="AprilAire")
        info.configure_char("SerialNumber", value="local")
        info.configure_char("Identify", setter_callback=self._identify)

        service = self.add_preload_service("Thermostat")
        self.current_temp = service.configure_char("CurrentTemperature", value=20.0)
        self.target_temp = service.configure_char(
            "TargetTemperature",
            value=21.0,
            setter_callback=self._on_target,
            properties={"minStep": 0.5},
        )
        self.target_mode = service.configure_char(
            "TargetHeatingCoolingState",
            setter_callback=self._on_mode,
        )
        self.current_mode = service.configure_char("CurrentHeatingCoolingState")
        self.display_units = service.configure_char("TemperatureDisplayUnits")
        self.display_units.set_value(1 if display_unit == "F" else 0, should_notify=False)
        # HAP-python's built-in thermostat service only includes the required
        # characteristics. Auto mode and humidity are optional characteristics
        # in the HomeKit spec, so they are added here.
        self.humidity = self._optional_char(service, "CurrentRelativeHumidity", value=0)
        self.heat_threshold = self._optional_char(
            service,
            "HeatingThresholdTemperature",
            value=20.0,
            setter=self._on_heat,
            properties={"minStep": 0.5},
        )
        self.cool_threshold = self._optional_char(
            service,
            "CoolingThresholdTemperature",
            value=24.0,
            setter=self._on_cool,
            properties={"minStep": 0.5},
        )
        humidity_sensor = self.add_preload_service("HumiditySensor")
        self.humidity_sensor = humidity_sensor.configure_char("CurrentRelativeHumidity")
        self.humidity_sensor.set_value(0, should_notify=False)

    def _optional_char(self, service, name, value, setter=None, properties=None):
        characteristic = self.driver.loader.get_char(name)
        service.add_characteristic(characteristic)
        # The service is already on the accessory, so this characteristic has to
        # join the same IID assignment or HomeKit cannot describe it.
        characteristic.broker = self
        self.iid_manager.assign(characteristic)
        if properties:
            characteristic.override_properties(properties)
        characteristic.set_value(value, should_notify=False)
        if setter is not None:
            characteristic.setter_callback = setter
        return characteristic

    def setup_message(self) -> None:
        """The window shows the QR. Keep the log to two lines."""
        pin = self.driver.state.pincode.decode()
        uri = self.xhm_uri()
        _LOG.info("HomeKit setup code %s", pin)
        _LOG.info("HomeKit setup URI %s", uri)

    def _identify(self, value: bool) -> None:
        if value:
            _LOG.info("Home app asked this thermostat to identify itself")

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        loop = self.driver.loop
        if not loop.is_running():
            coro.close()
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)

        def _done(done: asyncio.Future[None]) -> None:
            try:
                done.result()
            except Exception:
                _LOG.warning("Thermostat write failed", exc_info=True)

        future.add_done_callback(_done)

    def _on_mode(self, value: int) -> None:
        link = self._link
        if link is None:
            return
        self._spawn(link.set_mode(int(value)))

    def _on_target(self, value: float) -> None:
        link = self._link
        if link is None:
            return
        self._spawn(link.set_target(float(value)))

    def _on_heat(self, value: float) -> None:
        link = self._link
        if link is None:
            return
        self._spawn(link.set_heat(float(value)))

    def _on_cool(self, value: float) -> None:
        link = self._link
        if link is None:
            return
        self._spawn(link.set_cool(float(value)))

    async def run(self) -> None:
        self._stop.clear()
        self._stopped.clear()
        self._link = ThermostatLink(self._host, self._port, self.apply_view, _LOG)
        try:
            await self._link.run(self._stop)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._stop.is_set():
                _LOG.exception("Thermostat session ended")
        finally:
            self._stopped.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._link is not None:
            await self._link.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopped.wait(), timeout=5)

    def apply_view(self, view: ThermostatView) -> None:
        try:
            self._on_view(view)
        except Exception:
            _LOG.exception("Could not publish thermostat status")
        if not view.connected:
            return
        self._publish_identity(view)
        if view.current_c is not None:
            self._set(self.current_temp, clamp(view.current_c, -50.0, 100.0))
        target = displayed_target_c(view.hk_mode, view.heat_c, view.cool_c)
        if target is not None:
            self._set(self.target_temp, clamp(target, 10.0, 38.0))
        if view.hk_mode is not None:
            self._set(self.target_mode, view.hk_mode)
        self._set(self.current_mode, view.action)
        if view.heat_c is not None:
            self._set(self.heat_threshold, clamp(view.heat_c, 0.0, 25.0))
        if view.cool_c is not None:
            self._set(self.cool_threshold, clamp(view.cool_c, 10.0, 35.0))
        if view.humidity is not None:
            humidity = clamp(float(view.humidity), 0.0, 100.0)
            self._set(self.humidity, humidity)
            self._set(self.humidity_sensor, humidity)

    def _publish_identity(self, view: ThermostatView) -> None:
        if self._identity_sent or not view.mac:
            return
        info = self.get_service("AccessoryInformation")
        self._set(info.get_characteristic("SerialNumber"), view.mac)
        if view.model:
            self._set(info.get_characteristic("Model"), view.model)
        self._identity_sent = True
        if self.driver.state.paired:
            self.driver.config_changed()

    def _set(self, characteristic: object, value: object) -> None:
        try:
            characteristic.set_value(value)  # type: ignore[attr-defined]
        except Exception:
            _LOG.warning("HomeKit rejected %s=%s", characteristic, value, exc_info=True)
