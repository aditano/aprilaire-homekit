import asyncio
import socket
import threading
import time

from aprilaire_homekit.bridge import BridgeController
from aprilaire_homekit.paths import AppPaths
from aprilaire_homekit.settings import Settings
from aprilaire_homekit.status import StatusBoard
from tests.panel import Panel


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def test_homekit_bridge_reads_the_panel_and_stops(tmp_path):
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box: dict[str, object] = {}

    def _run_panel() -> None:
        asyncio.set_event_loop(loop)

        async def start() -> None:
            panel = Panel()
            box["panel"] = panel
            box["port"] = await panel.start()
            ready.set()
            await asyncio.Event().wait()

        try:
            loop.run_until_complete(start())
        except RuntimeError:
            return

    thread = threading.Thread(target=_run_panel, daemon=True)
    thread.start()
    assert ready.wait(5)
    paths = AppPaths(tmp_path / "data")
    board = StatusBoard()
    bridge = BridgeController(paths, board)
    settings = Settings(
        host="127.0.0.1",
        port=int(box["port"]),
        name="Test Thermostat",
        ui_port=0,
        homekit_port=_free_port(),
    )
    try:
        bridge.start(settings)
        deadline = time.time() + 12
        view = None
        while time.time() < deadline:
            view = board.snapshot_thermostat()
            if view and view.current_c == 22.0 and view.model == "8920W":
                break
            time.sleep(0.1)
        else:
            raise AssertionError(view)
        pin, uri = bridge.setup()
        assert pin and "-" in pin
        assert uri and uri.startswith("X-HM://")
        assert bridge.paired() is False
        running, error = board.snapshot_bridge()
        assert running
        assert error is None
    finally:
        bridge.stop()
        panel = box.get("panel")
        if panel is not None:
            asyncio.run_coroutine_threadsafe(panel.close(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=3)
