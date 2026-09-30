import asyncio
import json
import time
import urllib.error
import urllib.request

from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.runtime import Runtime
from aprilaire_homekit.settings import Settings
from tests.panel import Panel


class FakeBridge:
    def __init__(self) -> None:
        self.started_with = None
        self.running_flag = False
        self.paired_flag = False

    def running(self) -> bool:
        return self.running_flag

    def paired(self) -> bool:
        return self.paired_flag

    def setup(self):
        if not self.running_flag:
            return None, None
        if self.paired_flag:
            return "482-19-736", None
        return "482-19-736", "X-HM://00A0E3AL4K69Z"

    def start(self, settings) -> None:
        self.started_with = settings
        self.running_flag = True

    def stop(self) -> None:
        self.running_flag = False

    def restart(self, settings) -> None:
        self.stop()
        self.start(settings)


def _runtime(tmp_path) -> Runtime:
    paths = AppPaths(tmp_path / "data")
    runtime = Runtime(
        paths,
        Settings(ui_port=0, homekit_port=51826),
        home=tmp_path / "home",
    )
    runtime.bridge = FakeBridge()
    runtime.autostart_commands = False
    runtime.start(open_bridge=False)
    return runtime


def _request(runtime: Runtime, method: str, path: str, body: dict | None = None, header: bool = True):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Host": "127.0.0.1"}
    if header:
        headers["X-Aprilaire-Home"] = "1"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://127.0.0.1:{runtime.settings.ui_port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_page_and_local_only_api(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        status, page = _request(runtime, "GET", "/", header=False)
        assert status == 200
        assert b"Find your thermostat" in page
        status, body = _request(runtime, "GET", "/api/status", header=False)
        assert status == 403
        status, body = _request(runtime, "GET", "/api/status")
        payload = json.loads(body)
        assert payload["product"] == "aprilaire-homekit"
        assert payload["thermostat"]["host"] is None
        request = urllib.request.Request(
            f"http://127.0.0.1:{runtime.settings.ui_port}/api/status",
            headers={"Host": "evil.example", "X-Aprilaire-Home": "1"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("expected forbidden host")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        runtime.shutdown()


def test_scan_and_select_round_trip(tmp_path):
    runtime = _runtime(tmp_path)

    async def _start_panel():
        panel = Panel()
        port = await panel.start()
        return panel, port

    panel, port = asyncio.run_coroutine_threadsafe(_start_panel(), runtime.loop).result(timeout=5)
    runtime.scan_hosts_override = ["127.0.0.1"]
    runtime.scan_ports_override = (port,)
    try:
        status, _body = _request(runtime, "POST", "/api/scan", {})
        assert status == 200
        found = None
        for _ in range(80):
            _status, body = _request(runtime, "GET", "/api/status")
            payload = json.loads(body)
            if payload["scan"]["finished"] and payload["scan"]["devices"]:
                found = payload["scan"]["devices"][0]
                break
            time.sleep(0.1)
        assert found is not None
        assert found["model"] == "8920W"
        status, body = _request(
            runtime,
            "POST",
            "/api/select",
            {"host": found["host"], "port": found["port"], "name": "Woodland"},
        )
        assert status == 200
        saved = json.loads(runtime.config_path.read_text())
        assert saved["host"] == "127.0.0.1"
        assert saved["name"] == "Woodland"
        assert runtime.bridge.started_with.name == "Woodland"
        assert (tmp_path / "home" / ".config" / "systemd" / "user" / "aprilaire-homekit.service").is_file()
        status, body = _request(runtime, "POST", "/api/probe", {"host": "127.0.0.1", "port": port})
        assert json.loads(body)["device"]["mac"] == "02:11:22:33:44:55"
        _status, script = _request(runtime, "GET", "/app.js", header=False)
        assert b"Add to Apple Home" in script
    finally:
        try:
            asyncio.run_coroutine_threadsafe(panel.close(), runtime.loop).result(timeout=5)
        finally:
            runtime.shutdown()
