"""The network map and root-cause attribution.

A flat list of addresses cannot tell an engineer where a fault is: when a switch
fails, every device behind it also stops answering and raises its own alert. These
tests cover the uplink model that makes one failure read as one failure.
"""
import sqlite3
from pathlib import Path

import pytest

from ping_monitor.device_manager import DEVICE_TYPES, DeviceManager


@pytest.fixture()
def manager(tmp_path: Path) -> DeviceManager:
    return DeviceManager(db_path=str(tmp_path / "topology.sqlite3"))


@pytest.fixture()
def network(manager: DeviceManager) -> dict:
    """Router -> core switch -> lab switch -> three endpoints, plus a NAS."""
    built = {}
    built['router'] = manager.create_device(
        name="Edge-Router", ip_address="10.0.0.1", device_type="router")
    built['core'] = manager.create_device(
        name="Core-Switch", ip_address="10.0.0.10", device_type="switch",
        parent_id=built['router'].id)
    built['lab'] = manager.create_device(
        name="Lab-Switch", ip_address="10.0.0.11", device_type="switch",
        parent_id=built['core'].id)
    for i, (name, dtype) in enumerate(
            [("Lab-PC-01", "workstation"), ("Lab-PC-02", "workstation"),
             ("Lab-Printer", "printer")]):
        built[name] = manager.create_device(
            name=name, ip_address=f"10.0.2.{i + 10}", device_type=dtype,
            parent_id=built['lab'].id)
    built['nas'] = manager.create_device(
        name="NAS-01", ip_address="10.0.0.20", device_type="nas",
        parent_id=built['core'].id)
    return built


def bring_everything_up(manager: DeviceManager) -> None:
    for device in manager.list_devices():
        manager.record_ping_result(device.id, status="online", latency_ms=2.0)


def take_down(manager: DeviceManager, *devices) -> None:
    for device in devices:
        manager.record_ping_result(device.id, status="offline", latency_ms=None)


# ---------------------------------------------------------------- placement


def test_an_endpoint_cannot_be_added_before_any_uplink_exists(manager: DeviceManager) -> None:
    with pytest.raises(ValueError, match="Add a switch or router first"):
        manager.create_device(name="PC-1", ip_address="10.0.0.50", device_type="workstation")


def test_an_endpoint_must_name_its_uplink(manager: DeviceManager) -> None:
    manager.create_device(name="SW", ip_address="10.0.0.2", device_type="switch")

    with pytest.raises(ValueError, match="Choose the switch or router"):
        manager.create_device(name="PC-1", ip_address="10.0.0.50", device_type="workstation")


def test_infrastructure_may_sit_at_the_top_of_the_network(manager: DeviceManager) -> None:
    router = manager.create_device(name="R1", ip_address="10.0.0.1", device_type="router")
    assert router.parent_id is None


def test_an_endpoint_cannot_be_used_as_an_uplink(network, manager: DeviceManager) -> None:
    with pytest.raises(ValueError, match="cannot have devices connected"):
        manager.create_device(name="Behind-Printer", ip_address="10.0.9.9",
                              device_type="workstation",
                              parent_id=network['Lab-Printer'].id)


def test_unknown_device_types_are_rejected(manager: DeviceManager) -> None:
    with pytest.raises(ValueError, match="Device type must be one of"):
        manager.create_device(name="Mystery", ip_address="10.0.0.3", device_type="toaster")


def test_a_device_cannot_become_its_own_uplink(network, manager: DeviceManager) -> None:
    with pytest.raises(ValueError, match="cannot be its own uplink"):
        manager.set_device_parent(network['core'].id, network['core'].id)


def test_a_loop_is_refused(network, manager: DeviceManager) -> None:
    """Re-parenting the core switch under its own descendant would cycle."""
    with pytest.raises(ValueError, match="would create a loop"):
        manager.set_device_parent(network['core'].id, network['lab'].id)


def test_removing_a_switch_with_devices_behind_it_is_refused(
        network, manager: DeviceManager) -> None:
    with pytest.raises(ValueError, match="connect through this one"):
        manager.delete_device(network['lab'].id)

    for name in ("Lab-PC-01", "Lab-PC-02", "Lab-Printer"):
        manager.set_device_parent(network[name].id, network['core'].id)

    manager.delete_device(network['lab'].id)
    assert manager.get_device(network['lab'].id) is None


# ---------------------------------------------------------------- root cause


def test_a_failed_switch_marks_its_subtree_unreachable(network, manager: DeviceManager) -> None:
    bring_everything_up(manager)
    take_down(manager, network['lab'], network['Lab-PC-01'],
              network['Lab-PC-02'], network['Lab-Printer'])

    by_name = {n['name']: n for n in manager.get_topology()['nodes']}

    assert by_name['Lab-Switch']['derived_status'] == 'offline'
    for name in ("Lab-PC-01", "Lab-PC-02", "Lab-Printer"):
        assert by_name[name]['derived_status'] == 'unreachable'
        assert by_name[name]['blocked_by']['name'] == 'Lab-Switch'

    # Unaffected branches stay unaffected.
    assert by_name['NAS-01']['derived_status'] == 'online'
    assert by_name['Core-Switch']['derived_status'] == 'online'


def test_one_incident_names_the_switch_not_every_device_behind_it(
        network, manager: DeviceManager) -> None:
    bring_everything_up(manager)
    take_down(manager, network['lab'], network['Lab-PC-01'],
              network['Lab-PC-02'], network['Lab-Printer'])

    incidents = manager.get_topology()['incidents']

    assert len(incidents) == 1
    assert incidents[0]['device_name'] == 'Lab-Switch'
    assert incidents[0]['devices_affected'] == 3


def test_the_highest_failure_on_the_path_is_the_fault_domain(
        network, manager: DeviceManager) -> None:
    bring_everything_up(manager)
    # Both switches fail; the core is the one worth dispatching an engineer to.
    take_down(manager, network['core'], network['lab'], network['Lab-PC-01'])

    culprit = manager.find_fault_domain(network['Lab-PC-01'].id)
    assert culprit is not None and culprit.name == 'Core-Switch'


def test_a_device_that_answers_is_never_marked_unreachable(
        network, manager: DeviceManager) -> None:
    """A device still replying behind a switch that stopped answering ICMP is up."""
    take_down(manager, network['lab'])
    manager.record_ping_result(network['Lab-PC-01'].id, status="online", latency_ms=3.0)

    by_name = {n['name']: n for n in manager.get_topology()['nodes']}
    assert by_name['Lab-PC-01']['derived_status'] == 'online'


def test_descendants_are_counted_for_impact(network, manager: DeviceManager) -> None:
    assert len(manager.get_descendants(network['core'].id)) == 5
    assert len(manager.get_descendants(network['lab'].id)) == 3
    assert manager.get_descendants(network['nas'].id) == []


def test_alerts_for_devices_behind_a_failure_are_not_raised_as_critical(
        network, manager: DeviceManager) -> None:
    """The alert list must not bury the switch under the devices it took down."""
    from ping_monitor.ping_service import ping_service

    bring_everything_up(manager)

    switch = manager.get_device(network['lab'].id)
    ping_service._raise_status_alert(
        manager, switch, {'status': 'offline', 'latency_ms': None})
    manager.record_ping_result(switch.id, status='offline', latency_ms=None)

    for name in ("Lab-PC-01", "Lab-PC-02", "Lab-Printer"):
        device = manager.get_device(network[name].id)
        ping_service._raise_status_alert(
            manager, device, {'status': 'offline', 'latency_ms': None})
        manager.record_ping_result(device.id, status='offline', latency_ms=None)

    alerts = manager.get_alerts()
    critical = [a for a in alerts if a['severity'] == 'critical']

    assert len(critical) == 1
    assert 'Lab-Switch' in critical[0]['message']
    assert '3 device(s) behind it' in critical[0]['message']

    downstream = [a for a in alerts if a['alert_type'] == 'unreachable_via_uplink']
    assert len(downstream) == 3
    assert all(a['severity'] == 'info' for a in downstream)


def test_topology_reports_devices_not_yet_placed(manager: DeviceManager) -> None:
    """Devices migrated from before the topology model start unplaced, not hidden."""
    manager.create_device(name="SW", ip_address="10.0.0.2", device_type="switch")
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute(
            "INSERT INTO devices (name, ip_address, device_type, created_at, updated_at)"
            " VALUES ('Legacy-PC', '10.0.0.77', 'workstation', '2024-01-01T00:00:00+00:00',"
            " '2024-01-01T00:00:00+00:00')"
        )

    topology = manager.get_topology()
    unplaced = {n['name'] for n in topology['nodes'] if n['id'] in topology['unplaced']}
    assert unplaced == {'Legacy-PC'}


def test_every_device_type_declares_whether_it_can_carry_traffic() -> None:
    for key, meta in DEVICE_TYPES.items():
        assert isinstance(meta['infrastructure'], bool), key
        assert meta['label'], key
