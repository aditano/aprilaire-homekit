"""Per-user files. Nothing here is committed and nothing leaves the computer."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("APRILAIRE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AprilAire Home"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "AprilAire Home"
    xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(xdg) / "aprilaire-homekit"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def discover(cls) -> AppPaths:
        return cls(default_data_dir())

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def homekit_state(self) -> Path:
        return self.root / "homekit.state"

    @property
    def homekit_secrets(self) -> Path:
        return self.root / "homekit.json"

    @property
    def log_file(self) -> Path:
        return self.root / "bridge.log"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
