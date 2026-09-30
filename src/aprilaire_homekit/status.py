"""Thread-safe status the window polls."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from aprilaire_homekit.discovery import FoundThermostat
from aprilaire_homekit.mapping import ThermostatView


@dataclass
class ScanState:
    running: bool = False
    finished: bool = False
    scanned: int = 0
    total: int = 0
    error: str | None = None
    devices: list[FoundThermostat] = field(default_factory=list)


class StatusBoard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.thermostat: ThermostatView | None = None
        self.bridge_running = False
        self.bridge_error: str | None = None
        self.scan = ScanState()

    def set_bridge(self, running: bool, error: str | None = None) -> None:
        with self._lock:
            self.bridge_running = running
            self.bridge_error = error

    def set_thermostat(self, view: ThermostatView) -> None:
        with self._lock:
            if view.connected or self.thermostat is None:
                self.thermostat = view
                return
            previous = self.thermostat
            self.thermostat = ThermostatView(
                connected=False,
                raw_mode=previous.raw_mode,
                hk_mode=previous.hk_mode,
                action=previous.action,
                current_c=previous.current_c,
                heat_c=previous.heat_c,
                cool_c=previous.cool_c,
                humidity=previous.humidity,
                model=previous.model,
                model_id=previous.model_id,
                name=previous.name,
                mac=previous.mac,
                last_error=view.last_error,
            )

    def scan_started(self, total: int) -> None:
        with self._lock:
            self.scan = ScanState(running=True, total=total)

    def scan_progress(self, scanned: int, total: int, devices: list[FoundThermostat]) -> None:
        with self._lock:
            self.scan.running = True
            self.scan.scanned = scanned
            self.scan.total = total
            self.scan.devices = list(devices)

    def scan_finished(self, devices: list[FoundThermostat], error: str | None = None) -> None:
        with self._lock:
            self.scan.running = False
            self.scan.finished = True
            self.scan.devices = list(devices)
            self.scan.error = error
            if self.scan.total and self.scan.scanned < self.scan.total and error is None:
                self.scan.scanned = self.scan.total

    def snapshot_scan(self) -> ScanState:
        with self._lock:
            scan = self.scan
            return ScanState(
                running=scan.running,
                finished=scan.finished,
                scanned=scan.scanned,
                total=scan.total,
                error=scan.error,
                devices=list(scan.devices),
            )

    def snapshot_thermostat(self) -> ThermostatView | None:
        with self._lock:
            return self.thermostat

    def snapshot_bridge(self) -> tuple[bool, str | None]:
        with self._lock:
            return self.bridge_running, self.bridge_error
