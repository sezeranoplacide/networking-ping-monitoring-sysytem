"""Guards against the defect that made the shipped database unusable.

A column added to a CREATE TABLE has no effect on a database that already exists.
Unless it is also declared in SCHEMA_ADDITIONS, every query naming it fails with
'no such column' — which is how the device list, the monitoring loop and manual
ping all broke at once. See SYSTEM_AUDIT.md, finding C1.
"""
import re
import sqlite3
from pathlib import Path

import pytest

from ping_monitor import device_manager as dm_module
from ping_monitor.device_manager import DeviceManager

SOURCE = Path(dm_module.__file__).read_text(encoding="utf-8")


def declared_tables() -> dict[str, list[str]]:
    """Every column named in a CREATE TABLE statement in the module."""
    tables = {}
    for match in re.finditer(
        r'CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\s*\)\s*"""', SOURCE, re.S
    ):
        table, body = match.group(1), match.group(2)
        columns = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("FOREIGN KEY", "PRIMARY KEY", "UNIQUE(")):
                continue
            columns.append(line.split()[0])
        tables[table] = columns
    return tables


def test_every_declared_column_exists_on_a_fresh_database(tmp_path: Path) -> None:
    manager = DeviceManager(db_path=str(tmp_path / "fresh.sqlite3"))

    with sqlite3.connect(manager.db_path) as conn:
        for table, columns in declared_tables().items():
            live = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing = [c for c in columns if c not in live]
            assert not missing, f"{table} is missing {missing}"


def test_a_database_from_an_older_version_is_migrated(tmp_path: Path) -> None:
    """Build the original 2024-era schema, then open it with the current code."""
    db_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ip_address TEXT NOT NULL,
                interval INTEGER NOT NULL DEFAULT 5,
                timeout INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_latency_ms REAL,
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
        )
        conn.execute(
            """CREATE TABLE status_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                recorded_at TEXT NOT NULL)"""
        )
        conn.execute(
            """CREATE TABLE ping_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                latency_ms REAL,
                recorded_at TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO devices (name, ip_address, created_at, updated_at)"
            " VALUES ('Legacy-Router', '192.168.1.1', '2024-01-01T00:00:00+00:00',"
            " '2024-01-01T00:00:00+00:00')"
        )

    manager = DeviceManager(db_path=str(db_path))

    # The operations that used to fail with 'no such column'.
    devices = manager.list_devices()
    assert len(devices) == 1
    assert devices[0].name == "Legacy-Router"
    assert devices[0].group_name is None

    manager.record_ping_result(devices[0].id, status="online", latency_ms=5.0)
    assert manager.get_device(devices[0].id).status == "online"
    assert manager.get_status_timeline(devices[0].id)[0]["duration_seconds"] is not None
    assert manager.get_network_summary()["total_devices"] == 1


@pytest.mark.parametrize("table", sorted(dm_module.SCHEMA_ADDITIONS))
def test_migration_entries_name_real_columns(table: str, tmp_path: Path) -> None:
    """Every SCHEMA_ADDITIONS entry must match the table's CREATE statement."""
    declared = declared_tables().get(table)
    assert declared is not None, f"{table} has no CREATE TABLE statement"

    for column, _definition in dm_module.SCHEMA_ADDITIONS[table]:
        assert column in declared, f"{table}.{column} is migrated but never declared"
