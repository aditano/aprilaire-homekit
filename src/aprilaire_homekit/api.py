"""Local status page and control API. Bound to loopback only."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aprilaire_homekit import __version__
from aprilaire_homekit.mapping import format_temperature
from aprilaire_homekit.qr import setup_qr_svg

_UI = Path(__file__).resolve().parent / "ui"
_HEADER = "X-Aprilaire-Home"
_MAX_BODY = 16_384


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, runtime: Any) -> None:
        self.runtime = runtime
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:  # noqa: N802
        # No CORS headers. A web page on another site cannot read this API.
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            self._send(403, "application/json", _json({"error": "Local connections only."}))
            return
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                if self.headers.get(_HEADER) != "1":
                    raise ApiError("Missing local app header.", 403)
                status, content_type, payload = self.server.runtime.handle_api(method, path, self._body())  # type: ignore[attr-defined]
            elif method != "GET":
                raise ApiError("Not found.", 404)
            else:
                status, content_type, payload = _static(path)
        except ApiError as exc:
            status, content_type, payload = exc.status, "application/json", _json({"error": str(exc)})
        except Exception as exc:
            status, content_type, payload = 500, "application/json", _json({"error": str(exc) or "Internal error"})
        self._send(status, content_type, payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > _MAX_BODY:
            raise ApiError("Request is too large.")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError("Send JSON.") from exc
        if not isinstance(loaded, dict):
            raise ApiError("Send a JSON object.")
        return loaded

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


def build_status(runtime: Any) -> dict[str, Any]:
    settings = runtime.settings
    board = runtime.board
    view = board.snapshot_thermostat()
    running, error = board.snapshot_bridge()
    scan = board.snapshot_scan()
    pin, uri = runtime.bridge.setup()
    paired = runtime.bridge.paired()
    unit = settings.display_unit
    thermostat: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "connected": bool(view and view.connected),
        "model": view.model if view else None,
        "model_id": view.model_id if view else None,
        "mac": view.mac if view else None,
        "device_name": view.name if view else None,
        "mode": view.mode_label if view else None,
        "action": view.action_label if view and view.connected else None,
        "current": _format(view.current_c if view else None, unit),
        "heat": _format(view.heat_c if view else None, unit),
        "cool": _format(view.cool_c if view else None, unit),
        "humidity": view.humidity if view else None,
        "last_error": view.last_error if view else None,
        "display_unit": unit,
    }
    homekit = {
        "advertising": running,
        "paired": paired,
        "pin": pin if running and not paired else None,
        "uri": uri if running and not paired else None,
        "qr_svg": setup_qr_svg(uri) if uri and running and not paired else None,
        "port": settings.homekit_port,
    }
    autostart = runtime.autostart_state()
    return {
        "product": "aprilaire-homekit",
        "version": __version__,
        "accessory_name": settings.name,
        "paused": settings.paused,
        "bridge": {"running": running, "error": error},
        "thermostat": thermostat,
        "homekit": homekit,
        "scan": {
            "running": scan.running,
            "finished": scan.finished,
            "scanned": scan.scanned,
            "total": scan.total,
            "error": scan.error,
            "devices": [device.as_dict() for device in scan.devices],
        },
        "autostart": autostart,
        "log_path": str(runtime.paths.log_file),
    }


def _format(celsius: float | None, unit: str) -> str | None:
    return format_temperature(celsius, unit)


def _static(path: str) -> tuple[int, str, bytes]:
    name = "index.html" if path in {"", "/"} else path.lstrip("/")
    if name not in {"index.html", "app.css", "app.js"}:
        raise ApiError("Not found.", 404)
    file_path = _UI / name
    if not file_path.is_file():
        raise ApiError("The app window files are missing.", 500)
    content_type = {
        "index.html": "text/html; charset=utf-8",
        "app.css": "text/css; charset=utf-8",
        "app.js": "text/javascript; charset=utf-8",
    }[name]
    return 200, content_type, file_path.read_bytes()


def _json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")
