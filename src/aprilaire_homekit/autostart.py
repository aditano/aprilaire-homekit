"""Login items for macOS, Linux, and Windows.

The app writes the login item. Closing the window does not remove it.
`systemctl` and `launchctl` are best-effort: the file on disk is what the
toggle reports, and a failure is shown in the window instead of ignored.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

UNIT_NAME = "aprilaire-homekit.service"
LAUNCHD_LABEL = "com.aprilaire.homekit"
TASK_NAME = "AprilAire Home"


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    detail: str | None = None


def server_command(python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", "aprilaire_homekit", "--no-window"]


def render_systemd_unit(python: str, data_dir: Path) -> str:
    command = " ".join(server_command(python))
    return (
        "[Unit]\n"
        "Description=AprilAire Home\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        f"WorkingDirectory={data_dir}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_launchd_plist(python: str, log_file: Path) -> str:
    args = "".join(f"\n    <string>{_xml(part)}</string>" for part in server_command(python))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        "<plist version=\"1.0\">\n"
        "<dict>\n"
        "  <key>Label</key>\n"
        f"  <string>{LAUNCHD_LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>"
        f"{args}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "  <key>KeepAlive</key>\n"
        "  <dict>\n"
        "    <key>SuccessfulExit</key>\n"
        "    <false/>\n"
        "  </dict>\n"
        "  <key>StandardOutPath</key>\n"
        f"  <string>{_xml(str(log_file))}</string>\n"
        "  <key>StandardErrorPath</key>\n"
        f"  <string>{_xml(str(log_file))}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def render_windows_cmd(python: str) -> str:
    executable = python
    if executable.lower().endswith("python.exe"):
        pythonw = str(Path(executable).with_name("pythonw.exe"))
        executable = pythonw
    return f'@echo off\r\n"{executable}" -m aprilaire_homekit --no-window\r\n'


def linux_unit_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / UNIT_NAME


def linux_wants_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / "default.target.wants" / UNIT_NAME


def launchd_plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def windows_cmd_path(data_dir: Path) -> Path:
    return data_dir / "aprilaire-home.cmd"


def is_enabled(home: Path, data_dir: Path) -> bool:
    if sys.platform == "darwin":
        return launchd_plist_path(home).is_file()
    if sys.platform == "win32":
        return windows_cmd_path(data_dir).is_file()
    return linux_wants_path(home).is_file() or linux_unit_path(home).is_file()


def enable(
    home: Path,
    data_dir: Path,
    python: str | None = None,
    *,
    run_commands: bool = True,
) -> CommandResult:
    executable = python or sys.executable
    data_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path = launchd_plist_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_launchd_plist(executable, data_dir / "bridge.log"), encoding="utf-8")
        return CommandResult(True, "AprilAire Home will start the next time you log in.")
    if sys.platform == "win32":
        path = windows_cmd_path(data_dir)
        path.write_text(render_windows_cmd(executable), encoding="utf-8")
        if not run_commands:
            return CommandResult(True, "AprilAire Home will start the next time you log in.")
        completed = _run(
            [
                "schtasks",
                "/Create",
                "/TN",
                TASK_NAME,
                "/SC",
                "ONLOGON",
                "/RL",
                "LIMITED",
                "/F",
                "/TR",
                str(path),
            ]
        )
        if not completed.ok:
            return CommandResult(
                False,
                "The startup script was written, but Task Scheduler did not accept it. "
                + (completed.detail or ""),
            )
        return CommandResult(True, "AprilAire Home will start the next time you log in.")

    unit = linux_unit_path(home)
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(render_systemd_unit(executable, data_dir), encoding="utf-8")
    wants = linux_wants_path(home)
    wants.parent.mkdir(parents=True, exist_ok=True)
    if wants.is_symlink() or wants.exists():
        wants.unlink()
    wants.symlink_to(unit)
    if not run_commands:
        return CommandResult(True, "AprilAire Home will start the next time you log in.")
    reload = _run(["systemctl", "--user", "daemon-reload"])
    if not reload.ok:
        return CommandResult(
            True,
            "The login item is saved. systemctl could not reload just now; "
            "it will be picked up the next time you log in.",
        )
    return CommandResult(True, "AprilAire Home will start the next time you log in.")


def disable(home: Path, data_dir: Path, *, run_commands: bool = True) -> CommandResult:
    if sys.platform == "darwin":
        path = launchd_plist_path(home)
        if path.is_file():
            path.unlink()
        return CommandResult(True, None)
    if sys.platform == "win32":
        path = windows_cmd_path(data_dir)
        if path.is_file():
            path.unlink()
        if not run_commands:
            return CommandResult(True, None)
        completed = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        if not completed.ok and "cannot find" not in (completed.detail or "").lower():
            return CommandResult(False, completed.detail)
        return CommandResult(True, None)

    wants = linux_wants_path(home)
    unit = linux_unit_path(home)
    if wants.is_symlink() or wants.exists():
        wants.unlink()
    if unit.is_file():
        unit.unlink()
    if run_commands:
        _run(["systemctl", "--user", "disable", UNIT_NAME])
        _run(["systemctl", "--user", "daemon-reload"])
    return CommandResult(True, None)


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _run(command: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(False, str(exc))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return CommandResult(False, detail or f"Command failed ({completed.returncode})")
    return CommandResult(True, None)
