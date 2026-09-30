"""The process that serves the window and owns the HomeKit bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from aprilaire_homekit.api import ApiError, LocalServer, build_status
from aprilaire_homekit.autostart import disable, enable, is_enabled
from aprilaire_homekit.bridge import BridgeController
from aprilaire_homekit.discovery import (
    AUTOMATION_PORTS,
    FoundThermostat,
    default_scan_hosts,
    probe,
    scan_hosts,
)
from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.settings import (
    Settings,
    load_settings,
    resolve_config_path,
    save_settings,
    validate_host,
    validate_name,
    validate_port,
    validate_unit,
)
from aprilaire_homekit.status import StatusBoard

_LOG = logging.getLogger(__name__)


class Runtime:
    def __init__(
        self,
        paths: AppPaths,
        settings: Settings | None = None,
        home: Path | None = None,
    ) -> None:
        self.paths = paths
        self.paths.ensure()
        self.config_path = resolve_config_path(paths)
        self.settings = settings if settings is not None else load_settings(paths)
        self.home = home or Path.home()
        self.board = StatusBoard()
        self.bridge = BridgeController(paths, self.board)
        self.loop = asyncio.new_event_loop()
        self.scan_hosts_override: list[str] | None = None
        self.scan_ports_override: tuple[int, ...] | None = None
        self._loop_thread: threading.Thread | None = None
        self._httpd: LocalServer | None = None
        self._http_thread: threading.Thread | None = None
        self._scan_guard = threading.Lock()
        self._exit = os._exit
        self.autostart_commands = True
        self.autostart_detail: str | None = None

    def start(self, *, open_bridge: bool = True) -> None:
        if self._loop_thread is None:
            self._loop_thread = threading.Thread(
                target=self._run_loop, name="aprilaire-loop", daemon=True
            )
            self._loop_thread.start()
        if self._httpd is None:
            self._httpd = LocalServer(self.settings.ui_port, self)
            self.settings.ui_port = int(self._httpd.server_address[1])
            self._http_thread = threading.Thread(
                target=self._httpd.serve_forever, name="aprilaire-http", daemon=True
            )
            self._http_thread.start()
        if open_bridge and self.settings.host and not self.settings.paused:
            self.bridge.start(self.settings)

    def shutdown(self) -> None:
        try:
            self.bridge.stop()
        except Exception:
            _LOG.exception("Bridge stop failed during shutdown")
        if self._httpd is not None:
            self._httpd.shutdown()
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def handle_api(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, str, bytes]:
        if method == "GET" and path == "/api/status":
            return 200, "application/json", _bytes(build_status(self))
        if method != "POST":
            raise ApiError("Not found.", 404)
        if path == "/api/scan":
            self._begin_scan()
            return 200, "application/json", _bytes({"ok": True})
        if path == "/api/probe":
            return 200, "application/json", _bytes(self._probe(body))
        if path == "/api/select":
            return 200, "application/json", _bytes(self._select(body))
        if path == "/api/start":
            self._start_bridge()
            return 200, "application/json", _bytes(build_status(self))
        if path == "/api/stop":
            self._stop_bridge()
            return 200, "application/json", _bytes(build_status(self))
        if path == "/api/autostart":
            return 200, "application/json", _bytes(self._set_autostart(bool(body.get("enabled"))))
        if path == "/api/name":
            self._rename(body.get("name"))
            return 200, "application/json", _bytes(build_status(self))
        if path == "/api/display-unit":
            self._set_unit(body.get("unit"))
            return 200, "application/json", _bytes(build_status(self))
        if path == "/api/forget":
            self._forget()
            return 200, "application/json", _bytes(build_status(self))
        if path == "/api/reset-pairing":
            self._reset_pairing()
            return 200, "application/json", _bytes({"ok": True})
        if path == "/api/quit":
            threading.Thread(target=self._quit, name="aprilaire-quit", daemon=True).start()
            return 200, "application/json", _bytes({"ok": True})
        raise ApiError("Not found.", 404)

    def autostart_state(self) -> dict[str, Any]:
        try:
            enabled = is_enabled(self.home, self.paths.root)
        except Exception as exc:
            return {"enabled": False, "supported": True, "detail": str(exc)}
        return {"enabled": enabled, "supported": True, "detail": self.autostart_detail}

    def _save(self) -> None:
        save_settings(self.config_path, self.settings)

    def _begin_scan(self) -> None:
        with self._scan_guard:
            if self.board.snapshot_scan().running:
                return
            self.board.scan_started(0)
        future = asyncio.run_coroutine_threadsafe(self._scan(), self.loop)

        def _done(done: asyncio.Future[None]) -> None:
            try:
                done.result()
            except Exception as exc:
                _LOG.exception("Network scan failed")
                self.board.scan_finished([], str(exc) or "The network scan failed.")

        future.add_done_callback(_done)

    async def _scan(self) -> None:
        hosts = (
            list(self.scan_hosts_override)
            if self.scan_hosts_override is not None
            else default_scan_hosts()
        )
        ports = self.scan_ports_override or AUTOMATION_PORTS
        if not hosts:
            self.board.scan_finished(
                [],
                "This computer did not report a local network. Enter the thermostat IP instead.",
            )
            return
        self.board.scan_started(len(hosts) * len(ports))
        skip: set[tuple[str, int]] = set()
        running, _error = self.board.snapshot_bridge()
        if running and self.settings.host:
            skip.add((self.settings.host, self.settings.port))

        def progress(scanned: int, total: int, devices: list[FoundThermostat]) -> None:
            self.board.scan_progress(scanned, total, devices)

        found = await scan_hosts(hosts, ports, progress, skip)
        if self.settings.host and (self.settings.host, self.settings.port) in skip:
            view = self.board.snapshot_thermostat()
            already = FoundThermostat(
                host=self.settings.host,
                port=self.settings.port,
                mac=(view.mac if view and view.mac else "connected"),
                model=view.model if view else None,
                model_id=view.model_id if view else None,
                name=view.name if view else None,
            )
            if all(item.mac != already.mac or item.host != already.host for item in found):
                found.insert(0, already)
        self.board.scan_finished(found)

    def _probe(self, body: dict[str, Any]) -> dict[str, Any]:
        host = validate_host(str(body.get("host") or ""))
        port = validate_port(body.get("port"), default=8000)
        future = asyncio.run_coroutine_threadsafe(probe(host, port), self.loop)
        try:
            result = future.result(timeout=12)
        except Exception as exc:
            raise ApiError(str(exc) or "Could not reach that address.") from exc
        if result.device is None:
            raise ApiError(result.error or "That address is not an AprilAire thermostat.")
        return {"device": result.device.as_dict()}

    def _select(self, body: dict[str, Any]) -> dict[str, Any]:
        host = validate_host(str(body.get("host") or ""))
        port = validate_port(body.get("port"), default=8000)
        self.settings.host = host
        self.settings.port = port
        if body.get("name"):
            self.settings.name = validate_name(body.get("name"))
        self.settings.paused = False
        self._save()
        self.bridge.restart(self.settings)
        autostart = self._set_autostart(True)
        return {"ok": True, "autostart": autostart}

    def _start_bridge(self) -> None:
        if not self.settings.host:
            raise ApiError("Choose a thermostat first.")
        self.settings.paused = False
        self._save()
        if not self.bridge.running():
            self.bridge.start(self.settings)

    def _stop_bridge(self) -> None:
        self.settings.paused = True
        self._save()
        self.bridge.stop()

    def _rename(self, name: object) -> None:
        self.settings.name = validate_name(name)
        self._save()
        if self.bridge.running():
            self.bridge.restart(self.settings)

    def _set_unit(self, unit: object) -> None:
        self.settings.display_unit = validate_unit(unit)
        self._save()
        if self.bridge.running():
            self.bridge.restart(self.settings)

    def _forget(self) -> None:
        self.bridge.stop()
        self.settings.host = None
        self.settings.paused = False
        self._save()

    def _reset_pairing(self) -> None:
        resume = bool(self.settings.host) and not self.settings.paused
        self.bridge.stop()
        if self.paths.homekit_state.is_file():
            self.paths.homekit_state.unlink()
        if resume:
            self.bridge.start(self.settings)

    def _set_autostart(self, enabled: bool) -> dict[str, Any]:
        try:
            result = (
                enable(self.home, self.paths.root, run_commands=self.autostart_commands)
                if enabled
                else disable(self.home, self.paths.root, run_commands=self.autostart_commands)
            )
        except Exception as exc:
            return {"enabled": self.autostart_state()["enabled"], "supported": True, "detail": str(exc)}
        problem = None
        if not result.ok:
            problem = result.detail or "The login item could not be changed."
        elif result.detail and "could not" in result.detail.lower():
            problem = result.detail
        self.autostart_detail = problem
        return self.autostart_state()

    def _quit(self) -> None:
        time.sleep(0.3)
        self.shutdown()
        self._exit(0)


def _bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")
