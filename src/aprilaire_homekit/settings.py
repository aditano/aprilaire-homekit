"""Settings saved by the desktop app, with optional env and YAML overrides."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aprilaire_homekit.paths import AppPaths

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


@dataclass
class Settings:
    host: str | None = None
    port: int = 8000
    name: str = "AprilAire"
    display_unit: str = "F"
    homekit_port: int = 51826
    ui_port: int = 8765
    paused: bool = False
    pin: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "name": self.name,
            "display_unit": self.display_unit,
            "homekit_port": self.homekit_port,
            "ui_port": self.ui_port,
            "paused": self.paused,
        }


def validate_host(host: str) -> str:
    text = host.strip()
    if not text or text == "CHANGE_ME" or "://" in text or any(ch in text for ch in " /\\"):
        raise ValueError("Enter the thermostat IP address, for example 192.168.1.50.")
    try:
        ipaddress.ip_address(text)
        return text
    except ValueError:
        pass
    if _HOST_RE.fullmatch(text):
        return text
    raise ValueError("That does not look like an IP address or hostname.")


def validate_port(value: object, *, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("The port must be a number.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("The port must be between 1 and 65535.")
    return port


def validate_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "AprilAire"
    if len(text) > 32:
        raise ValueError("The Home app name must be 32 characters or fewer.")
    return text


def validate_unit(value: object) -> str:
    text = str(value or "F").strip().upper()
    if text in {"F", "FAHRENHEIT"}:
        return "F"
    if text in {"C", "CELSIUS"}:
        return "C"
    raise ValueError("Temperature display must be F or C.")


def _apply_mapping(settings: Settings, raw: dict[str, Any]) -> None:
    thermostat = raw.get("thermostat") if isinstance(raw.get("thermostat"), dict) else raw
    homekit = raw.get("homekit") if isinstance(raw.get("homekit"), dict) else raw
    if thermostat.get("host"):
        settings.host = validate_host(str(thermostat["host"]))
    if "port" in thermostat and thermostat.get("port") not in (None, ""):
        settings.port = validate_port(thermostat.get("port"), default=settings.port)
    if homekit.get("name"):
        settings.name = validate_name(homekit.get("name"))
    if homekit.get("display_unit") or raw.get("display_unit"):
        settings.display_unit = validate_unit(
            homekit.get("display_unit") or raw.get("display_unit")
        )
    if homekit.get("port") and "homekit_port" not in raw and "thermostat" in raw:
        settings.homekit_port = validate_port(homekit.get("port"), default=settings.homekit_port)
    if raw.get("homekit_port"):
        settings.homekit_port = validate_port(raw.get("homekit_port"), default=settings.homekit_port)
    if raw.get("ui_port"):
        settings.ui_port = validate_port(raw.get("ui_port"), default=settings.ui_port)
    if "paused" in raw:
        settings.paused = bool(raw.get("paused"))
    pin = homekit.get("pin") if isinstance(homekit, dict) else None
    if isinstance(pin, str) and pin.strip():
        settings.pin = pin.strip()


def resolve_config_path(paths: AppPaths, env: dict[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    override = source.get("APRILAIRE_CONFIG")
    if override:
        return Path(override).expanduser()
    return paths.config_file


def load_settings(paths: AppPaths, env: dict[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    settings = Settings()
    file_path = resolve_config_path(paths, source)
    if file_path.is_file():
        text = file_path.read_text(encoding="utf-8")
        if file_path.suffix.lower() in {".yaml", ".yml"}:
            loaded = yaml.safe_load(text) or {}
        else:
            loaded = json.loads(text)
        if not isinstance(loaded, dict):
            raise ValueError(f"{file_path} must contain a mapping.")
        _apply_mapping(settings, loaded)
    if not settings.host and source.get("APRILAIRE_HOST"):
        settings.host = validate_host(source["APRILAIRE_HOST"])
    if source.get("APRILAIRE_PORT"):
        settings.port = validate_port(source["APRILAIRE_PORT"], default=settings.port)
    if source.get("HOMEKIT_NAME"):
        settings.name = validate_name(source["HOMEKIT_NAME"])
    if source.get("HOMEKIT_PORT"):
        settings.homekit_port = validate_port(source["HOMEKIT_PORT"], default=settings.homekit_port)
    if source.get("APRILAIRE_UI_PORT"):
        settings.ui_port = validate_port(source["APRILAIRE_UI_PORT"], default=settings.ui_port)
    if source.get("HOMEKIT_DISPLAY_UNIT"):
        settings.display_unit = validate_unit(source["HOMEKIT_DISPLAY_UNIT"])
    if source.get("HOMEKIT_PIN"):
        settings.pin = source["HOMEKIT_PIN"].strip()
    return settings


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        payload = {
            "thermostat": {"host": settings.host, "port": settings.port},
            "homekit": {
                "name": settings.name,
                "port": settings.homekit_port,
                "display_unit": settings.display_unit,
            },
            "ui_port": settings.ui_port,
            "paused": settings.paused,
        }
        text = yaml.safe_dump(payload, sort_keys=False)
    else:
        text = json.dumps(settings.to_json(), indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
