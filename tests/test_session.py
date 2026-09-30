import asyncio
import contextlib

from pyaprilaire.const import Action, Attribute
from pyaprilaire.packet import Packet

from aprilaire_homekit.mapping import HK_COOL
from aprilaire_homekit.session import ThermostatLink
from tests.panel import Panel


def test_link_reads_state_and_sends_a_mode_change():
    async def body():
        panel = Panel()
        port = await panel.start()
        views = []
        link = ThermostatLink("127.0.0.1", port, views.append)
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        try:
            for _ in range(80):
                if views and views[-1].current_c == 22.0 and views[-1].model == "8920W":
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(f"thermostat state did not arrive: {views[-1] if views else None}")
            view = views[-1]
            assert view.connected
            assert view.humidity == 40
            assert view.heat_c == 21.5
            assert view.mode_label == "Heat"
            assert view.action_label == "Heating"
            await link.set_mode(HK_COOL)
            await link.set_heat(20)
            for _ in range(40):
                packets = list(Packet.parse(bytes(panel.received)))
                modes = [
                    packet.data.get(Attribute.MODE)
                    for packet in packets
                    if packet.action == Action.WRITE
                ]
                heats = [
                    packet.data.get(Attribute.HEAT_SETPOINT)
                    for packet in packets
                    if packet.action == Action.WRITE
                ]
                if 3 in modes and 20.0 in heats:
                    return
                await asyncio.sleep(0.1)
            raise AssertionError(panel.received.hex())
        finally:
            stop.set()
            await link.close()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await panel.close()

    asyncio.run(body())
