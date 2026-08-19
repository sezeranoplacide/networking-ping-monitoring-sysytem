import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ping_monitor.device_manager import DeviceManager, normalize_target


@pytest.fixture()
def manager(tmp_path: Path) -> DeviceManager:
    db_path = tmp_path / "ping_monitor.sqlite3"
    return DeviceManager(db_path=str(db_path))


def seed_ping(manager: DeviceManager, device_id: int, *, age_hours: float,
              status: str = "online", latency: float = 1.0) -> None:
    """Insert a ping result at a known age, in the format the app writes."""
    recorded = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).replace(microsecond=0)
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute(
            "INSERT INTO ping_results (device_id, status, latency_ms, packet_loss, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (device_id, status, latency, 0, recorded.isoformat()),
        )


# ---------------------------------------------------------------- devices


def test_create_device_and_list_devices(manager: DeviceManager) -> None:
    device = manager.create_device(name="Router-Lab1", ip_address="192.168.1.1", device_type="switch")

    assert device.name == "Router-Lab1"
    assert device.ip_address == "192.168.1.1"
    assert device.status == "unknown"

    devices = manager.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "Router-Lab1"


def test_status_changes_are_logged(manager: DeviceManager) -> None:
    device = manager.create_device(name="PC-Lab-05", ip_address="192.168.1.55", device_type="switch")

    manager.record_ping_result(device.id, status="online", latency_ms=3.2)
    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    updated_device = manager.get_device(device.id)
    assert updated_device is not None
    assert updated_device.status == "offline"
    # A failed ping has no latency; 0.0 would render as a very fast response.
    assert updated_device.last_latency_ms is None

    with sqlite3.connect(manager.db_path) as conn:
        history_rows = conn.execute(
            "SELECT status, latency_ms FROM ping_results WHERE device_id = ? ORDER BY id",
            (device.id,),
        ).fetchall()
        transition_rows = conn.execute(
            "SELECT from_status, to_status FROM status_changes WHERE device_id = ? ORDER BY id",
            (device.id,),
        ).fetchall()

    assert [row[0] for row in history_rows] == ["online", "offline"]
    assert history_rows[1][1] is None
    assert transition_rows == [("unknown", "online"), ("online", "offline")]


def test_unknown_status_values_are_rejected(manager: DeviceManager) -> None:
    """Regression: any string was accepted, then vanished from every count query."""
    device = manager.create_device(name="Vocab", ip_address="10.0.0.3", device_type="switch")

    with pytest.raises(ValueError, match="Status must be one of"):
        manager.record_ping_result(device.id, status="banana", latency_ms=1.0)

    with pytest.raises(ValueError):
        manager.record_ping_result(device.id, status="up", latency_ms=1.0)


def test_invalid_addresses_are_rejected(manager: DeviceManager) -> None:
    """Regression: the IP field accepted markup, which reached the dashboard."""
    with pytest.raises(ValueError):
        manager.create_device(name="XSS", ip_address="'); alert(1);//", device_type="switch")

    with pytest.raises(ValueError):
        manager.create_device(name="Bad", ip_address="999.1.1.1", device_type="switch")


def test_hostnames_and_ipv6_are_accepted(manager: DeviceManager) -> None:
    assert normalize_target("router.local") == "router.local"
    assert normalize_target("2001:db8::1") == "2001:db8::1"
    assert normalize_target(" 192.168.1.1 ") == "192.168.1.1"

    device = manager.create_device(name="By-Name", ip_address="switch.example.com", device_type="switch")
    assert device.ip_address == "switch.example.com"


def test_duplicate_addresses_are_rejected(manager: DeviceManager) -> None:
    manager.create_device(name="First", ip_address="10.0.0.7", device_type="switch")

    with pytest.raises(ValueError, match="already monitored"):
        manager.create_device(name="Second", ip_address="10.0.0.7", device_type="switch")


def test_deleting_a_device_removes_its_history(manager: DeviceManager) -> None:
    """Regression: foreign keys were off, so ON DELETE CASCADE never fired."""
    device = manager.create_device(name="Temp", ip_address="10.0.0.1", device_type="switch")
    manager.record_ping_result(device.id, status="online", latency_ms=5.0)
    manager.create_alert(device.id, "test", "info", "message")

    manager.delete_device(device.id)

    with sqlite3.connect(manager.db_path) as conn:
        for table in ("ping_results", "alerts", "status_changes"):
            remaining = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE device_id = ?", (device.id,)
            ).fetchone()[0]
            assert remaining == 0, f"{table} kept orphan rows"


# ---------------------------------------------------------------- measurements


def test_uptime_and_latency_are_measured_not_assumed(manager: DeviceManager) -> None:
    """Regression: the API returned a hardcoded 100% for every device."""
    device = manager.create_device(name="Metrics", ip_address="10.0.0.4", device_type="switch")
    assert manager.get_device(device.id).uptime_percentage is None

    manager.record_ping_result(device.id, status="online", latency_ms=10.0)
    manager.record_ping_result(device.id, status="online", latency_ms=30.0)
    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    updated = manager.get_device(device.id)
    assert updated.total_requests == 3
    assert updated.successful_requests == 2
    assert updated.failed_requests == 1
    assert updated.uptime_percentage == pytest.approx(66.67, abs=0.01)
    assert updated.min_latency_ms == 10.0
    assert updated.max_latency_ms == 30.0
    assert updated.avg_latency_ms == pytest.approx(20.0)


def test_last_seen_only_moves_when_the_device_answers(manager: DeviceManager) -> None:
    device = manager.create_device(name="Seen", ip_address="10.0.0.5", device_type="switch")

    manager.record_ping_result(device.id, status="online", latency_ms=4.0)
    answered_at = manager.get_device(device.id).last_seen_at

    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    assert manager.get_device(device.id).last_seen_at == answered_at


def test_statistics_window_excludes_older_rows(manager: DeviceManager) -> None:
    """Regression: comparing ISO timestamps to SQLite's datetime('now') format
    made a 24-hour window return roughly 48 hours of rows."""
    device = manager.create_device(name="Window", ip_address="10.0.0.6", device_type="switch")

    seed_ping(manager, device.id, age_hours=2, latency=111.0)
    seed_ping(manager, device.id, age_hours=30, latency=222.0)
    seed_ping(manager, device.id, age_hours=40, latency=333.0)
    seed_ping(manager, device.id, age_hours=60, latency=444.0)

    stats = manager.get_device_statistics(device.id, hours=24)

    assert stats["total_pings"] == 1
    assert stats["max_latency_ms"] == 111.0


def test_status_timeline_records_how_long_the_state_held(manager: DeviceManager) -> None:
    """Regression: duration_seconds was declared and rendered but never written."""
    device = manager.create_device(name="Duration", ip_address="10.0.0.8", device_type="switch")

    manager.record_ping_result(device.id, status="online", latency_ms=1.0)
    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    timeline = manager.get_status_timeline(device.id)
    assert len(timeline) == 2
    assert all(entry["duration_seconds"] is not None for entry in timeline)


def test_network_summary_counts_each_device_once(manager: DeviceManager) -> None:
    """Regression: offline was total - online, so the figures summed above total."""
    up = manager.create_device(name="Up", ip_address="10.0.1.1", device_type="switch")
    down = manager.create_device(name="Down", ip_address="10.0.1.2", device_type="switch")
    manager.create_device(name="Never polled", ip_address="10.0.1.3", device_type="switch")

    manager.record_ping_result(up.id, status="online", latency_ms=8.0)
    manager.record_ping_result(down.id, status="offline", latency_ms=None)

    summary = manager.get_network_summary()

    assert summary["total_devices"] == 3
    assert summary["online_devices"] + summary["offline_devices"] + summary["unknown_devices"] == 3
    assert summary["unknown_devices"] == 1
    # Averaged over devices that answered, not dragged toward zero by the ones that didn't.
    assert summary["average_latency_ms"] == 8.0
    assert summary["network_health_percentage"] == 50.0


# ---------------------------------------------------------------- notifications


def test_escalation_runs_and_promotes_stale_critical_notices(manager: DeviceManager) -> None:
    """Regression: this raised NameError and queried a column that did not exist."""
    device = manager.create_device(name="Escalate", ip_address="10.0.2.1", device_type="switch")
    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(microsecond=0).isoformat()
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE notifications SET created_at = ? WHERE severity = 'critical'", (old,))

    assert manager.escalate_unacknowledged_notifications(older_than_minutes=5) == 1
    # Escalating twice must not duplicate the notice.
    assert manager.escalate_unacknowledged_notifications(older_than_minutes=5) == 0


def test_escalation_never_escalates_its_own_notices(manager: DeviceManager) -> None:
    """Regression: the escalation notice was itself critical and unacknowledged, so
    each pass escalated the previous escalation and the message nested one level
    deeper every minute."""
    device = manager.create_device(name="Loop", ip_address="10.0.3.1", device_type="switch")
    manager.record_ping_result(device.id, status="offline", latency_ms=None)

    def age_everything():
        old = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(microsecond=0).isoformat()
        with sqlite3.connect(manager.db_path) as conn:
            conn.execute("UPDATE notifications SET created_at = ?", (old,))

    age_everything()
    assert manager.escalate_unacknowledged_notifications(older_than_minutes=5) == 1

    # However many times it runs, the one alert produces exactly one escalation.
    for _ in range(5):
        age_everything()
        assert manager.escalate_unacknowledged_notifications(older_than_minutes=5) == 0

    notices = [n for n in manager.get_notifications(limit=100)
               if (n['title'] or '').startswith('Escalation')]
    assert len(notices) == 1
    assert notices[0]['message'].count('Escalation triggered for:') == 1
