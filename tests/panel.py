"""A tiny AprilAire automation peer for tests. Not a full thermostat."""

from __future__ import annotations

import asyncio
import contextlib

from pyaprilaire.const import Action, Attribute, FunctionalDomain
from pyaprilaire.packet import Packet


def status_burst() -> bytes:
    packets = [
        Packet(
            Action.READ_RESPONSE,
            FunctionalDomain.IDENTIFICATION,
            2,
            data={Attribute.MAC_ADDRESS: [0x02, 0x11, 0x22, 0x33, 0x44, 0x55]},
        ),
        Packet(
            Action.READ_RESPONSE,
            FunctionalDomain.IDENTIFICATION,
            1,
            data={
                Attribute.HARDWARE_REVISION: 1,
                Attribute.FIRMWARE_MAJOR_REVISION: 1,
                Attribute.FIRMWARE_MINOR_REVISION: 2,
                Attribute.PROTOCOL_MAJOR_REVISION: 1,
                Attribute.MODEL_NUMBER: 6,
                Attribute.GAINSPAN_FIRMWARE_MAJOR_REVISION: 1,
                Attribute.GAINSPAN_FIRMWARE_MINOR_REVISION: 0,
            },
        ),
        Packet(
            Action.READ_RESPONSE,
            FunctionalDomain.CONTROL,
            1,
            data={
                Attribute.MODE: 2,
                Attribute.FAN_MODE: 1,
                Attribute.HEAT_SETPOINT: 21.5,
                Attribute.COOL_SETPOINT: 24.0,
            },
        ),
        Packet(
            Action.READ_RESPONSE,
            FunctionalDomain.SENSORS,
            2,
            data={
                Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_STATUS: 0,
                Attribute.INDOOR_TEMPERATURE_CONTROLLING_SENSOR_VALUE: 22.0,
                Attribute.OUTDOOR_TEMPERATURE_CONTROLLING_SENSOR_STATUS: 3,
                Attribute.OUTDOOR_TEMPERATURE_CONTROLLING_SENSOR_VALUE: 0.0,
                Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_STATUS: 0,
                Attribute.INDOOR_HUMIDITY_CONTROLLING_SENSOR_VALUE: 40,
                Attribute.OUTDOOR_HUMIDITY_CONTROLLING_SENSOR_STATUS: 3,
                Attribute.OUTDOOR_HUMIDITY_CONTROLLING_SENSOR_VALUE: 0,
            },
        ),
        Packet(
            Action.READ_RESPONSE,
            FunctionalDomain.STATUS,
            6,
            data={
                Attribute.HEATING_EQUIPMENT_STATUS: 2,
                Attribute.COOLING_EQUIPMENT_STATUS: 0,
                Attribute.PROGRESSIVE_RECOVERY: 0,
                Attribute.FAN_STATUS: 0,
            },
        ),
    ]
    return b"".join(packet.serialize() for packet in packets)


class Panel:
    def __init__(self) -> None:
        self.received = bytearray()
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        replied = False
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self.received.extend(data)
                if not replied:
                    writer.write(status_burst())
                    await writer.drain()
                    replied = True
        except Exception:
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
