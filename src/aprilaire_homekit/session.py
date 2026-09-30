"""One local TCP session to an AprilAire thermostat.

The thermostat allows a single automation client. A second connection can
freeze the panel until power is cycled. This module never opens two sockets,
and it closes the socket before giving up.

pyaprilaire 0.8.1 starts a queue task that does not end when the socket drops,
and its reconnect path can stick after a refused connection. The tracked
protocol below cancels those tasks, and the link supervisor owns reconnects.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from pyaprilaire.client import AprilaireClient, _AprilaireClientProtocol
from pyaprilaire.const import QUEUE_FREQUENCY, Action, Attribute, FunctionalDomain
from pyaprilaire.packet import Packet

from aprilaire_homekit.mapping import (
    SetpointWrite,
    ThermostatView,
    aprilaire_mode_from_homekit,
    setpoint_write_cool,
    setpoint_write_for_target,
    setpoint_write_heat,
    view_from_data,
)

_LOG = logging.getLogger(__name__)

_BOOKKEEPING = {
    Attribute.AVAILABLE,
    Attribute.CONNECTED,
    Attribute.CONNECTING,
    Attribute.RECONNECTING,
    Attribute.STOPPED,
}

# The socket silently stops delivering change-of-state after a long uptime.
# The library author reconnects about once an hour for that reason.
RECYCLE_SECONDS = 60 * 60
REFRESH_SECONDS = 60
RETRY_MIN_SECONDS = 5
RETRY_MAX_SECONDS = 60


class ThermostatUnavailable(RuntimeError):
    """The thermostat socket is not open, so a write was not sent."""


class _TrackedProtocol(_AprilaireClientProtocol):
    """Protocol that cancels its own queue task when the socket closes."""

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.logger.info("Connected to the AprilAire thermostat")
        self.transport = transport
        self._empty_packet_queue()
        self._workers = [
            asyncio.ensure_future(self._queue_loop()),
            asyncio.ensure_future(self._update_status()),
        ]

    def connection_lost(self, exc: Exception | None) -> None:
        for task in getattr(self, "_workers", ()):
            task.cancel()
        super().connection_lost(exc)


class LinkClient(AprilaireClient):
    """AprilaireClient whose reconnect is delegated to ThermostatLink."""

    def __init__(
        self,
        host: str,
        port: int,
        callback: Callable[[dict[str, Any]], None],
        logger: logging.Logger,
        on_lost: Callable[[], Any],
    ) -> None:
        super().__init__(host, port, callback, logger, None, None)
        self._on_lost = on_lost

    def create_protocol(self) -> _TrackedProtocol:
        return _TrackedProtocol(self.data_received, self._on_lost, self.logger)

    async def read_identification(self) -> None:
        # Model number is identification attribute 1. pyaprilaire 0.8.1 reads
        # the MAC (attribute 2) on connect, but not the model, unless COS
        # happens to deliver it. Ask for both up front.
        await self.protocol._send_packet(
            Packet(Action.READ_REQUEST, FunctionalDomain.IDENTIFICATION, 1)
        )
        await self.protocol._send_packet(
            Packet(Action.READ_REQUEST, FunctionalDomain.IDENTIFICATION, 2)
        )


class ThermostatLink:
    """Keep one socket up, publish views, and apply HomeKit writes."""

    def __init__(
        self,
        host: str,
        port: int,
        on_view: Callable[[ThermostatView], None],
        logger: logging.Logger | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._on_view = on_view
        self._logger = logger or _LOG
        self._data: dict[str, Any] = {}
        self._connected = False
        self._client: LinkClient | None = None
        self._lost = asyncio.Event()
        self._last_hk_mode: int | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    def _emit(self, *, last_error: str | None = None) -> None:
        view = view_from_data(
            self._data, connected=self._connected, last_error=last_error
        )
        self._last_hk_mode = view.hk_mode
        try:
            self._on_view(view)
        except Exception:
            self._logger.exception("Status listener failed")

    def _on_data(self, data: dict[str, Any]) -> None:
        if data.get(Attribute.CONNECTED) is True:
            self._connected = True
        if data.get(Attribute.AVAILABLE) is False or data.get(Attribute.CONNECTED) is False:
            self._connected = False
        self._data.update(
            {key: value for key, value in data.items() if key not in _BOOKKEEPING}
        )
        self._emit()

    async def _on_lost(self) -> None:
        self._connected = False
        self._lost.set()

    def _make_client(self) -> LinkClient:
        return LinkClient(
            self.host,
            self.port,
            self._on_data,
            self._logger,
            self._on_lost,
        )

    async def run(self, stop: asyncio.Event) -> None:
        backoff = RETRY_MIN_SECONDS
        while not stop.is_set():
            self._lost.clear()
            client = self._make_client()
            self._client = client
            try:
                await asyncio.wait_for(client.start_listen_once(), timeout=8)
            except Exception as exc:
                self._connected = False
                message = _connect_message(exc)
                self._logger.warning("Thermostat connection failed: %s", message)
                self._emit(last_error=message)
                await _wait(stop, backoff)
                backoff = min(backoff * 2, RETRY_MAX_SECONDS)
                continue

            backoff = RETRY_MIN_SECONDS
            self._connected = True
            self._emit()
            if client.protocol is not None:
                with contextlib.suppress(Exception):
                    await client.read_identification()
            lost_task = asyncio.create_task(self._lost.wait())
            recycle_task = asyncio.create_task(_wait(stop, RECYCLE_SECONDS))
            refresh_task = asyncio.create_task(self._refresh(stop))
            stop_task = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait(
                    {lost_task, recycle_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in (lost_task, recycle_task, refresh_task, stop_task):
                    with contextlib.suppress(Exception):
                        task.cancel()
                with contextlib.suppress(Exception):
                    client.stop_listen()
                # Let the panel release the single automation socket.
                if not stop.is_set():
                    with contextlib.suppress(Exception):
                        await asyncio.sleep(min(QUEUE_FREQUENCY, 0.2))
            self._connected = False
            if stop.is_set():
                self._emit()
                break
            self._logger.info("Reconnecting to the thermostat")
            self._emit(last_error="Reconnecting")
            await _wait(stop, 2)

    async def close(self) -> None:
        client = self._client
        self._connected = False
        self._lost.set()
        if client is not None:
            with contextlib.suppress(Exception):
                client.stop_listen()

    async def _refresh(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await _wait(stop, REFRESH_SECONDS)
            client = self._client
            if client is None or not client.connected or client.protocol is None:
                continue
            try:
                await client.read_sensors()
                await client.read_control()
                await client.read_thermostat_status()
            except Exception:
                self._logger.debug("Periodic thermostat refresh failed", exc_info=True)

    async def set_mode(self, hk_mode: int) -> None:
        client = self._require_client()
        await client.update_mode(aprilaire_mode_from_homekit(hk_mode))
        await client.read_control()

    async def set_target(self, celsius: float) -> None:
        await self._write_setpoint(setpoint_write_for_target(self._last_hk_mode, celsius))

    async def set_heat(self, celsius: float) -> None:
        await self._write_setpoint(setpoint_write_heat(celsius))

    async def set_cool(self, celsius: float) -> None:
        await self._write_setpoint(setpoint_write_cool(celsius))

    async def _write_setpoint(self, write: SetpointWrite) -> None:
        client = self._require_client()
        await client.update_setpoint(write.cool, write.heat)
        await client.read_control()

    def _require_client(self) -> LinkClient:
        client = self._client
        if client is None or not self._connected or client.protocol is None:
            raise ThermostatUnavailable("The thermostat is not connected.")
        return client


def _connect_message(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "The thermostat did not answer in time."
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "refused" in lowered or "errno 111" in lowered or "errno 61" in lowered:
        return (
            "The thermostat refused the connection. In the installer menu, set "
            "Automation Enable to Automation System. 8800-series thermostats, "
            "including the 8920W when that menu is present, listen on port 8000."
        )
    return text


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    if seconds <= 0 or stop.is_set():
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return
