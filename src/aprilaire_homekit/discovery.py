"""Find AprilAire thermostats on the local network.

There is no documented mDNS or SSDP advertisement for the automation socket.
Discovery is a TCP check of the automation ports on the local subnet, then the
same identification read the bridge uses (MAC address, and model when the
panel answers). A device that is still in Aprilaire Cloud mode does not open
the port and will not appear.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import logging
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass

import psutil
from pyaprilaire.const import Attribute

from aprilaire_homekit.mapping import format_mac, model_name
from aprilaire_homekit.session import LinkClient

_LOG = logging.getLogger(__name__)

AUTOMATION_PORTS = (8000, 7000)
_CONNECT_TIMEOUT = 0.4
_IDENTIFY_TIMEOUT = 3.5
_HOST_CAP = 1024
_VIRTUAL_PREFIXES = (
    "docker",
    "br-",
    "veth",
    "virbr",
    "tun",
    "tap",
    "zt",
    "tailscale",
    "wg",
    "vmnet",
    "vbox",
    "lo",
)


@dataclass(frozen=True)
class InterfaceAddress:
    name: str
    address: str
    netmask: str


@dataclass(frozen=True)
class FoundThermostat:
    host: str
    port: int
    mac: str
    model: str | None
    model_id: int | None
    name: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "mac": self.mac,
            "model": self.model,
            "model_id": self.model_id,
            "name": self.name,
        }


@dataclass(frozen=True)
class ProbeResult:
    device: FoundThermostat | None
    error: str | None


def outbound_ipv4() -> str | None:
    """Best-guess LAN address. UDP connect does not send a packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 9))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    try:
        parsed = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError:
        return None
    if parsed.is_loopback:
        return None
    return address


def local_interfaces() -> list[InterfaceAddress]:
    found: list[InterfaceAddress] = []
    for name, addresses in psutil.net_if_addrs().items():
        for item in addresses:
            if item.family != socket.AF_INET or not item.address or not item.netmask:
                continue
            found.append(InterfaceAddress(name=name, address=item.address, netmask=item.netmask))
    return found


def hosts_for(
    interfaces: list[InterfaceAddress],
    outbound_ip: str | None = None,
) -> list[str]:
    """IPv4 hosts worth probing. Wide masks are narrowed to a /24."""
    real = [item for item in interfaces if not _is_virtual(item.name)]
    chosen = real or list(interfaces)
    blocks: list[tuple[int, list[str]]] = []
    for item in chosen:
        try:
            ipaddress.IPv4Address(item.address)
        except ipaddress.AddressValueError:
            continue
        if ipaddress.IPv4Address(item.address).is_loopback:
            continue
        if ipaddress.IPv4Address(item.address).is_link_local:
            continue
        hosts = _hosts_on(item.address, item.netmask)
        if not hosts:
            continue
        preferred = outbound_ip is not None and (
            outbound_ip == item.address or outbound_ip in hosts
        )
        blocks.append((0 if preferred else 1, hosts))
    blocks.sort(key=lambda item: item[0])
    ordered: list[str] = []
    seen: set[str] = set()
    for _priority, hosts in blocks:
        for host in hosts:
            if host in seen:
                continue
            seen.add(host)
            ordered.append(host)
            if len(ordered) >= _HOST_CAP:
                return ordered
    return ordered


def _is_virtual(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(_VIRTUAL_PREFIXES) or lowered == "lo0"


def _hosts_on(address: str, netmask: str) -> list[str]:
    try:
        network = ipaddress.IPv4Network(f"{address}/{netmask}", strict=False)
    except (ipaddress.AddressValueError, ValueError):
        return []
    if network.prefixlen < 24:
        network = ipaddress.IPv4Network(f"{address}/24", strict=False)
    return [str(host) for host in network.hosts()]


async def tcp_open(host: str, port: int, timeout: float = _CONNECT_TIMEOUT) -> bool:
    writer: asyncio.StreamWriter | None = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout
        )
        return True
    except Exception:
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def probe(host: str, port: int, timeout: float = _IDENTIFY_TIMEOUT) -> ProbeResult:
    """Open one automation session long enough to read identity, then close it."""
    found: dict[str, object] = {}
    ready = asyncio.Event()

    def on_data(data: dict[str, object]) -> None:
        found.update(data)
        if Attribute.MAC_ADDRESS in found:
            ready.set()

    async def on_lost() -> None:
        return None

    client = LinkClient(host, port, on_data, _LOG, on_lost)
    try:
        await asyncio.wait_for(client.start_listen_once(), timeout=timeout)
    except TimeoutError:
        return ProbeResult(None, _timeout_message(host, port))
    except OSError as exc:
        return ProbeResult(None, _os_message(exc, host, port))
    except Exception as exc:
        _LOG.debug("Probe failed for %s:%s", host, port, exc_info=True)
        return ProbeResult(None, _os_message(exc, host, port))

    try:
        if client.protocol is not None:
            await client.read_identification()
        try:
            await asyncio.wait_for(ready.wait(), timeout=timeout)
        except TimeoutError:
            return ProbeResult(
                None,
                f"{host} answered on port {port}, but it did not speak the "
                "AprilAire automation protocol.",
            )
        return ProbeResult(_device_from_data(host, port, found), None)
    finally:
        try:
            client.stop_listen()
        except Exception:
            _LOG.debug("Closing probe socket failed", exc_info=True)
        await asyncio.sleep(0.05)


async def scan_hosts(
    hosts: list[str],
    ports: tuple[int, ...] = AUTOMATION_PORTS,
    on_progress: Callable[[int, int, list[FoundThermostat]], None] | None = None,
    skip: set[tuple[str, int]] | None = None,
) -> list[FoundThermostat]:
    """Scan hosts. `on_progress(scanned, total, found)` is called from the loop."""
    skip = skip or set()
    targets = [(host, port) for host in hosts for port in ports if (host, port) not in skip]
    total = len(targets)
    open_targets: list[tuple[str, int]] = []
    scanned = 0
    found: list[FoundThermostat] = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(64)

    async def check(host: str, port: int) -> None:
        nonlocal scanned
        async with semaphore:
            is_open = await tcp_open(host, port)
        async with lock:
            scanned += 1
            if is_open:
                open_targets.append((host, port))
            snapshot = scanned
            report = scanned == total or scanned % 20 == 0
        if on_progress and report:
            on_progress(snapshot, total, list(found))

    if targets:
        await asyncio.gather(*(check(host, port) for host, port in targets))
    elif on_progress:
        on_progress(0, 0, [])

    seen: set[str] = set()
    for host, port in open_targets:
        # The pre-check already closed its socket. Give the panel a moment
        # before the real identification session.
        await asyncio.sleep(0.3)
        result = await probe(host, port)
        if result.device is None or result.device.mac in seen:
            continue
        seen.add(result.device.mac)
        found.append(result.device)
        if on_progress:
            on_progress(total, total, list(found))
    if on_progress:
        on_progress(total, total, list(found))
    return found


def advertised_address() -> str | None:
    """LAN address HomeKit should advertise. Prefer a private interface."""
    try:
        interfaces = local_interfaces()
    except Exception:
        _LOG.exception("Could not list network interfaces")
        return outbound_ipv4()
    outbound = outbound_ipv4()
    real = [item for item in interfaces if not _is_virtual(item.name)]
    chosen = real or interfaces
    private: str | None = None
    for item in chosen:
        try:
            ip = ipaddress.IPv4Address(item.address)
        except ipaddress.AddressValueError:
            continue
        if ip.is_loopback or ip.is_link_local:
            continue
        if outbound and (outbound == item.address or outbound in _hosts_on(item.address, item.netmask)):
            return item.address
        if private is None and ip.is_private:
            private = item.address
    return private or outbound


def default_scan_hosts() -> list[str]:
    try:
        interfaces = local_interfaces()
    except Exception:
        _LOG.exception("Could not list network interfaces")
        return []
    return hosts_for(interfaces, outbound_ipv4())


def _device_from_data(host: str, port: int, data: dict[str, object]) -> FoundThermostat | None:
    mac_raw = data.get(Attribute.MAC_ADDRESS)
    if not isinstance(mac_raw, str) or not mac_raw:
        return None
    model_id = data.get(Attribute.MODEL_NUMBER)
    if model_id is not None:
        model_id = int(model_id)
    name = data.get(Attribute.NAME)
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = None
    return FoundThermostat(
        host=host,
        port=port,
        mac=format_mac(mac_raw),
        model=model_name(model_id if isinstance(model_id, int) else None),
        model_id=model_id if isinstance(model_id, int) else None,
        name=name,
    )


def _timeout_message(host: str, port: int) -> str:
    return (
        f"{host} did not answer on port {port}. This computer and the thermostat "
        "need to be on the same network."
    )


def _os_message(exc: Exception, host: str, port: int) -> str:
    err_no = getattr(exc, "errno", None)
    text = str(exc).lower()
    refused = err_no in {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN}
    if sys.platform == "win32":
        refused = refused or err_no in {10061, 10065, 10051}
    if refused or "refused" in text or "no route" in text:
        return (
            f"Nothing at {host} is accepting automation connections on port {port}. "
            "On the thermostat, hold Contractor Info for about 10 seconds and set "
            "Automation Enable to Automation System (not Aprilaire Cloud). The 8920W "
            "installation manual does not list that setting; if it is missing, this "
            "app cannot control that panel over the local network. 8800-series panels "
            "that do support it use port 8000."
        )
    if isinstance(exc, TimeoutError) or "timed out" in text:
        return _timeout_message(host, port)
    return f"Could not reach {host}:{port}. {exc}"
