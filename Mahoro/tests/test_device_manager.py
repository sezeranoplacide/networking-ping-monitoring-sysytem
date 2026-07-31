import sqlite3
from pathlib import Path

import pytest

from ping_monitor.device_manager import DeviceManager


@pytest.fixture()
def manager(tmp_path: Path) -> DeviceManager:
    db_path = tmp_path / "ping_monitor.sqlite3"
    return DeviceManager(db_path=str(db_path))


def test_create_device_and_list_devices(manager: DeviceManager) -> None:
    device = manager.create_device(name="Router-Lab1", ip_address="192.168.1.1")

    assert device.name == "Router-Lab1"
    assert device.ip_address == "192.168.1.1"
    assert device.status == "unknown"

    devices = manager.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "Router-Lab1"


def test_status_changes_are_logged(manager: DeviceManager) -> None:
    device = manager.create_device(name="PC-Lab-05", ip_address="192.168.1.55")

    manager.record_ping_result(device.id, status="up", latency_ms=3.2)
    manager.record_ping_result(device.id, status="down", latency_ms=None)

    updated_device = manager.get_device(device.id)
    assert updated_device is not None
    assert updated_device.status == "down"
    assert updated_device.last_latency_ms == 0.0

    with sqlite3.connect(manager.db_path) as conn:
        history_rows = conn.execute(
            "SELECT device_id, status, latency_ms FROM ping_results WHERE device_id = ? ORDER BY id",
            (device.id,),
        ).fetchall()
        transition_rows = conn.execute(
            "SELECT device_id, from_status, to_status FROM status_changes WHERE device_id = ? ORDER BY id",
            (device.id,),
        ).fetchall()

    assert len(history_rows) == 2
    assert history_rows[0][1] == "up"
    assert history_rows[1][1] == "down"
    assert len(transition_rows) == 2
    assert transition_rows[0][1] == "unknown"
    assert transition_rows[0][2] == "up"
    assert transition_rows[1][1] == "up"
    assert transition_rows[1][2] == "down"
