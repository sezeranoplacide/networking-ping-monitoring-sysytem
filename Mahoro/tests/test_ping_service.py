import pytest

from ping_monitor.ping_service import PingService


@pytest.fixture()
def service() -> PingService:
    return PingService()


def test_router_unreachable_reply_is_not_online(service: PingService) -> None:
    """Regression: Windows ping exits 0 when a router answers 'unreachable', so
    trusting the exit code alone drew dead devices green."""
    output = (
        "Pinging 10.1.1.5 with 32 bytes of data:\r\n"
        "Reply from 192.168.1.1: Destination host unreachable.\r\n"
    )

    result = service._parse_ping_output(output, 0, 0.01)

    assert result["status"] == "offline"
    assert result["packet_loss"] == 100
    assert "unreachable" in result["error"].lower()


def test_ttl_expired_reply_is_not_online(service: PingService) -> None:
    result = service._parse_ping_output("Reply from 10.0.0.1: TTL expired in transit.\r\n", 0, 0.01)
    assert result["status"] == "offline"


def test_timed_out_reply_is_offline(service: PingService) -> None:
    output = (
        "Pinging 10.255.255.1 with 32 bytes of data:\r\n"
        "Request timed out.\r\n"
        "    Packets: Sent = 1, Received = 0, Lost = 1 (100% loss),\r\n"
    )
    result = service._parse_ping_output(output, 1, 0.01)
    assert result["status"] == "offline"
    assert result["latency_ms"] is None


def test_genuine_reply_is_online_with_latency(service: PingService) -> None:
    output = (
        "Pinging 192.168.1.1 with 32 bytes of data:\r\n"
        "Reply from 192.168.1.1: bytes=32 time=10ms TTL=64\r\n"
        "    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),\r\n"
    )

    result = service._parse_ping_output(output, 0, 0.01)

    assert result["status"] == "online"
    assert result["latency_ms"] == 10.0
    assert result["packet_loss"] == 0


def test_unix_reply_is_parsed(service: PingService) -> None:
    output = (
        "64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=12.4 ms\n"
        "1 packets transmitted, 1 received, 0% packet loss\n"
    )
    result = service._parse_ping_output(output, 0, 0.01)
    assert result["status"] == "online"
    assert result["latency_ms"] == pytest.approx(12.4)


def test_targets_accept_hostnames_and_ipv6(service: PingService) -> None:
    assert service._is_valid_ip("192.168.1.1")
    assert service._is_valid_ip("router.local")
    assert service._is_valid_ip("2001:db8::1")
    assert not service._is_valid_ip("999.1.1.1")
    assert not service._is_valid_ip("'); alert(1);//")
