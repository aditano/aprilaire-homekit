import json

import pytest

from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.pin import generate_pin, is_valid_pin, load_homekit_secrets, normalize_pin
from aprilaire_homekit.settings import Settings, load_settings, save_settings, validate_host


def test_generated_pins_are_acceptable():
    for _ in range(20):
        pin = generate_pin()
        assert is_valid_pin(pin)


def test_obvious_pins_are_rejected():
    assert not is_valid_pin("123-45-678")
    assert not is_valid_pin("000-00-000")
    assert not is_valid_pin("876-54-321")
    assert normalize_pin("48219736") == "482-19-736"
    with pytest.raises(ValueError):
        normalize_pin("12345678")


def test_secrets_file_keeps_the_pin(tmp_path):
    path = tmp_path / "homekit.json"
    pin, setup_id = load_homekit_secrets(path)
    assert setup_id is None
    again, _ = load_homekit_secrets(path)
    assert again == pin
    overridden, _ = load_homekit_secrets(path, "482-19-736")
    assert overridden == "482-19-736"


def test_host_validation():
    assert validate_host("192.168.1.50") == "192.168.1.50"
    with pytest.raises(ValueError):
        validate_host("http://192.168.1.50")
    with pytest.raises(ValueError):
        validate_host("")


def test_json_settings_round_trip(tmp_path):
    paths = AppPaths(tmp_path)
    paths.ensure()
    settings = Settings(host="192.168.1.20", port=8000, name="Hall", display_unit="C")
    save_settings(paths.config_file, settings)
    loaded = load_settings(paths, env={})
    assert loaded.host == "192.168.1.20"
    assert loaded.display_unit == "C"
    assert loaded.name == "Hall"
    assert json.loads(paths.config_file.read_text())["paused"] is False


def test_yaml_and_env_seed(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "thermostat:\n  host: 10.0.0.8\n  port: 8000\nhomekit:\n  name: Woodland\n  port: 51827\n  display_unit: F\n",
        encoding="utf-8",
    )
    paths = AppPaths(tmp_path / "data")
    loaded = load_settings(paths, env={"APRILAIRE_CONFIG": str(config)})
    assert loaded.host == "10.0.0.8"
    assert loaded.homekit_port == 51827
    assert loaded.name == "Woodland"
    seeded = load_settings(paths, env={"APRILAIRE_HOST": "192.168.4.4", "APRILAIRE_PORT": "8000"})
    assert seeded.host == "192.168.4.4"
