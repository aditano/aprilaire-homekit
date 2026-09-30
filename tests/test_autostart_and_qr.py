from aprilaire_homekit.autostart import (
    disable,
    enable,
    is_enabled,
    render_launchd_plist,
    render_systemd_unit,
    render_windows_cmd,
)
from aprilaire_homekit.qr import setup_qr_svg


def test_login_items_point_at_the_windowless_bridge(tmp_path):
    unit = render_systemd_unit("/usr/bin/python3", tmp_path)
    assert "/usr/bin/python3 -m aprilaire_homekit --no-window" in unit
    assert "WantedBy=default.target" in unit
    plist = render_launchd_plist("/usr/bin/python3", tmp_path / "bridge.log")
    assert "com.aprilaire.homekit" in plist
    assert "--no-window" in plist
    script = render_windows_cmd(r"C:\Python\python.exe")
    assert "pythonw.exe" in script
    assert "--no-window" in script

    home = tmp_path / "home"
    data = tmp_path / "data"
    result = enable(home, data, python="/usr/bin/python3", run_commands=False)
    assert result.ok
    assert is_enabled(home, data)
    text = (home / ".config/systemd/user/aprilaire-homekit.service").read_text()
    assert "--no-window" in text
    disable(home, data, run_commands=False)
    assert not is_enabled(home, data)


def test_setup_qr_is_svg():
    svg = setup_qr_svg("X-HM://00A0E3AL4K69Z")
    assert svg.strip().startswith("<svg")
    assert "<script" not in svg.lower()
