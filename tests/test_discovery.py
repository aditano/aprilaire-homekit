import asyncio

from aprilaire_homekit.discovery import InterfaceAddress, hosts_for, probe
from tests.panel import Panel


def test_scan_list_skips_virtual_and_wide_masks():
    interfaces = [
        InterfaceAddress("lo", "127.0.0.1", "255.0.0.0"),
        InterfaceAddress("docker0", "172.17.0.1", "255.255.0.0"),
        InterfaceAddress("eth0", "192.168.1.40", "255.255.255.0"),
        InterfaceAddress("wlan0", "10.1.2.3", "255.255.0.0"),
    ]
    hosts = hosts_for(interfaces, "192.168.1.40")
    assert "127.0.0.1" not in hosts
    assert "172.17.0.1" not in hosts
    assert "192.168.1.40" in hosts
    assert "192.168.1.1" in hosts
    assert "10.1.2.3" in hosts
    assert "10.1.3.1" not in hosts
    assert hosts[0].startswith("192.168.1.")
    assert len(hosts) < 600


def test_probe_reads_model_and_mac():
    async def body():
        panel = Panel()
        port = await panel.start()
        try:
            return await probe("127.0.0.1", port, timeout=4)
        finally:
            await panel.close()

    result = asyncio.run(body())
    assert result.error is None
    assert result.device is not None
    assert result.device.model == "8920W"
    assert result.device.model_id == 6
    assert result.device.mac == "02:11:22:33:44:55"


def test_probe_refused_port_is_a_clear_error():
    result = asyncio.run(probe("127.0.0.1", 1, timeout=2))
    assert result.device is None
    assert result.error
    assert "Automation" in result.error
