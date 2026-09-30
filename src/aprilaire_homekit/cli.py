"""Command line entry. With no arguments this opens the window."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aprilaire_homekit import __version__
from aprilaire_homekit.discovery import probe
from aprilaire_homekit.launcher import open_window
from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.runtime import Runtime
from aprilaire_homekit.settings import load_settings

_LOG = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aprilaire-homekit",
        description="Connect an AprilAire thermostat to Apple Home on this computer.",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Run the bridge without opening a window.",
    )
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--config", help="Optional JSON or YAML settings file.")
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")
    check = sub.add_parser("check", help="Ask one thermostat for its model and MAC address.")
    check.add_argument("--host", required=True)
    check.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0
    if args.config:
        os.environ["APRILAIRE_CONFIG"] = str(Path(args.config).expanduser())
    if args.command == "check":
        return _check(args.host, args.port)
    paths = AppPaths.discover()
    paths.ensure()
    if args.no_window or args.background:
        configure_logging(paths.log_file)
        return _serve(paths)
    return _open(paths)


def configure_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )


def _serve(paths: AppPaths) -> int:
    settings = load_settings(paths)
    if server_is_ours(settings.ui_port):
        _LOG.info("AprilAire Home is already running")
        return 0
    runtime = Runtime(paths, settings)
    runtime.start()
    _LOG.info("AprilAire Home is listening on 127.0.0.1:%s", runtime.settings.ui_port)
    stop = threading.Event()

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        while not stop.wait(0.5):
            continue
    finally:
        runtime.shutdown()
    return 0


def _open(paths: AppPaths) -> int:
    settings = load_settings(paths)
    port = settings.ui_port
    if not server_is_ours(port):
        _spawn(paths)
        if not _wait_until_up(port):
            print(f"AprilAire Home did not start. The log is {paths.log_file}")
            return 1
    open_window(f"http://127.0.0.1:{port}/")
    return 0


def _spawn(paths: AppPaths) -> None:
    paths.ensure()
    log = paths.log_file.open("a", encoding="utf-8")
    command = [sys.executable, "-m", "aprilaire_homekit", "--no-window"]
    config = os.environ.get("APRILAIRE_CONFIG")
    if config:
        command.extend(["--config", config])
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "env": os.environ.copy(),
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    subprocess.Popen(command, **kwargs)


def _wait_until_up(port: int) -> bool:
    for _ in range(80):
        if server_is_ours(port):
            return True
        time.sleep(0.25)
    return False


def server_is_ours(port: int) -> bool:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/status",
        headers={"X-Aprilaire-Home": "1", "Host": "127.0.0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=0.4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    return payload.get("product") == "aprilaire-homekit"


def _check(host: str, port: int) -> int:
    result = asyncio.run(probe(host, port))
    if result.device is None:
        print(result.error or "No AprilAire thermostat answered.")
        return 1
    device = result.device
    print(f"Model: {device.model or 'AprilAire thermostat'}")
    print(f"MAC: {device.mac}")
    print(f"Address: {device.host}:{device.port}")
    if device.name:
        print(f"Name: {device.name}")
    return 0
