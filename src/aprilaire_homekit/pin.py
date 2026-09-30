"""HomeKit setup codes.

Apple rejects a handful of obvious codes. The code is only the pairing secret;
the long-term keys live in the HomeKit state file.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

_PIN_RE = re.compile(r"^\d{3}-\d{2}-\d{3}$")
_INVALID_DIGITS = {
    "00000000",
    "11111111",
    "22222222",
    "33333333",
    "44444444",
    "55555555",
    "66666666",
    "77777777",
    "88888888",
    "99999999",
    "12345678",
    "87654321",
}


def digits_of(pin: str) -> str:
    return pin.replace("-", "")


def is_valid_pin(pin: str) -> bool:
    if not _PIN_RE.fullmatch(pin):
        return False
    compact = digits_of(pin)
    if compact in _INVALID_DIGITS:
        return False
    if compact == "".join(str(n) for n in range(1, 9)):
        return False
    return True


def normalize_pin(pin: str) -> str:
    text = pin.strip()
    compact = text.replace("-", "").replace(" ", "")
    if len(compact) == 8 and compact.isdigit():
        text = f"{compact[:3]}-{compact[3:5]}-{compact[5:]}"
    if not is_valid_pin(text):
        raise ValueError(
            "The HomeKit code must look like 482-19-736 and cannot be "
            "000-00-000, 123-45-678, or 876-54-321."
        )
    return text


def generate_pin() -> str:
    while True:
        compact = "".join(str(secrets.randbelow(10)) for _ in range(8))
        pin = f"{compact[:3]}-{compact[3:5]}-{compact[5:]}"
        if is_valid_pin(pin):
            return pin


def load_homekit_secrets(path: Path, override_pin: str | None = None) -> tuple[str, str | None]:
    """Return (pin, setup_id). setup_id is None until HomeKit has created one."""
    saved_pin: str | None = None
    setup_id: str | None = None
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if isinstance(raw.get("pin"), str):
                saved_pin = raw["pin"]
            if isinstance(raw.get("setup_id"), str) and raw["setup_id"]:
                setup_id = raw["setup_id"]
    if override_pin:
        pin = normalize_pin(override_pin)
    elif saved_pin and is_valid_pin(saved_pin):
        pin = saved_pin
    else:
        pin = generate_pin()
    if pin != saved_pin:
        save_homekit_secrets(path, pin, setup_id)
    return pin, setup_id


def save_homekit_secrets(path: Path, pin: str, setup_id: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pin": pin, "setup_id": setup_id}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
