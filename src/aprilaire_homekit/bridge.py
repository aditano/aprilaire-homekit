"""Run the HomeKit driver on its own thread so the window can start and stop it."""

from __future__ import annotations

import asyncio
import logging
import threading

from pyhap.accessory_driver import AccessoryDriver

from aprilaire_homekit.accessory import AprilaireThermostat
from aprilaire_homekit.discovery import advertised_address
from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.pin import load_homekit_secrets, save_homekit_secrets
from aprilaire_homekit.settings import Settings
from aprilaire_homekit.status import StatusBoard

_LOG = logging.getLogger(__name__)


class BridgeController:
    def __init__(self, paths: AppPaths, board: StatusBoard) -> None:
        self._paths = paths
        self._board = board
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._driver: AccessoryDriver | None = None
        self._accessory: AprilaireThermostat | None = None
        self._ready = threading.Event()

    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and self._driver is not None

    def paired(self) -> bool:
        driver = self._driver
        if driver is None:
            return False
        try:
            return bool(driver.state.paired)
        except Exception:
            return False

    def setup(self) -> tuple[str | None, str | None]:
        accessory = self._accessory
        driver = self._driver
        if accessory is None or driver is None or not self.running():
            return None, None
        try:
            pin = driver.state.pincode.decode()
            if driver.state.paired:
                return pin, None
            return pin, accessory.xhm_uri()
        except Exception:
            _LOG.debug("Setup code is not ready", exc_info=True)
            return None, None

    def start(self, settings: Settings) -> None:
        if not settings.host:
            raise ValueError("Choose a thermostat first.")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._driver = None
            self._accessory = None
            thread = threading.Thread(
                target=self._run,
                args=(settings,),
                name="aprilaire-homekit",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        self._board.set_bridge(False, None)
        driver = self._driver
        thread = self._thread
        if driver is not None and driver.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(driver.async_stop(), driver.loop)
                future.result(timeout=8)
            except Exception:
                _LOG.info("HomeKit stop finished early", exc_info=True)
        if thread is not None:
            thread.join(timeout=8)
        with self._lock:
            self._driver = None
            self._accessory = None
        alive = thread is not None and thread.is_alive()
        self._board.set_bridge(False, "The bridge did not stop cleanly." if alive else None)

    def restart(self, settings: Settings) -> None:
        self.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            raise RuntimeError("The bridge is still shutting down. Wait a moment and try again.")
        self.start(settings)

    def _run(self, settings: Settings) -> None:
        try:
            self._paths.ensure()
            pin, setup_id = load_homekit_secrets(self._paths.homekit_secrets, settings.pin)
            address = advertised_address()
            driver = AccessoryDriver(
                port=settings.homekit_port,
                persist_file=str(self._paths.homekit_state),
                pincode=pin.encode(),
                address=address,
                listen_address="0.0.0.0",
            )
            accessory = AprilaireThermostat(
                driver,
                settings.name,
                settings.host or "",
                settings.port,
                settings.display_unit,
                self._board.set_thermostat,
            )
            driver.add_accessory(accessory)
            if setup_id:
                driver.state.setup_id = setup_id
            else:
                save_homekit_secrets(self._paths.homekit_secrets, pin, driver.state.setup_id)
            try:
                self._paths.homekit_state.chmod(0o600)
            except OSError:
                pass
            self._driver = driver
            self._accessory = accessory
            self._board.set_bridge(True, None)
            self._ready.set()
            driver.start()
        except Exception as exc:
            _LOG.exception("HomeKit bridge stopped")
            self._board.set_bridge(False, str(exc) or "The HomeKit bridge stopped.")
            return
        self._board.set_bridge(False, None)
