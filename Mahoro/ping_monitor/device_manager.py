from __future__ import annotations
import ipaddress
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pyotp
from werkzeug.security import check_password_hash, generate_password_hash

from . import paths

# The only statuses the rest of the system knows how to count. Anything else
# silently disappears from every dashboard query, so it is rejected at write time.
VALID_STATUSES = ('online', 'offline', 'unknown')

VALID_ROLES = ('admin', 'operator', 'network_engineer', 'viewer')

# What a device is determines both its icon and where it can sit in the topology.
# Infrastructure forwards traffic, so other devices can hang off it; an endpoint is
# a leaf and must be plugged into something.
DEVICE_TYPES = {
    'router':       {'label': 'Router',        'infrastructure': True},
    'switch':       {'label': 'Switch',        'infrastructure': True},
    'firewall':     {'label': 'Firewall',      'infrastructure': True},
    'access_point': {'label': 'Access Point',  'infrastructure': True},
    'server':       {'label': 'Server',        'infrastructure': False},
    'workstation':  {'label': 'Workstation',   'infrastructure': False},
    'printer':      {'label': 'Printer',       'infrastructure': False},
    'ip_phone':     {'label': 'Office Telephone', 'infrastructure': False},
    'camera':       {'label': 'IP Camera',     'infrastructure': False},
    'nas':          {'label': 'Storage / NAS', 'infrastructure': False},
    'other':        {'label': 'Other',         'infrastructure': False},
}

INFRASTRUCTURE_TYPES = tuple(k for k, v in DEVICE_TYPES.items() if v['infrastructure'])

# SQLite's CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
# so a database created by an older version keeps its original columns forever and
# every query naming a newer one fails with 'no such column'. Any column added after
# a table first shipped must be listed here.
#
# Adding a column to a CREATE TABLE above without adding it here is the bug that
# made the shipped database unusable — see SYSTEM_AUDIT.md, finding C1.
SCHEMA_ADDITIONS = {
    'devices': (
        ('group_name', 'TEXT'),
        ('min_latency_ms', 'REAL'),
        ('max_latency_ms', 'REAL'),
        ('avg_latency_ms', 'REAL'),
        ('uptime_percentage', 'REAL DEFAULT 100.0'),
        ('total_requests', 'INTEGER DEFAULT 0'),
        ('successful_requests', 'INTEGER DEFAULT 0'),
        ('failed_requests', 'INTEGER DEFAULT 0'),
        ('device_type', "TEXT NOT NULL DEFAULT 'other'"),
        # The uplink this device hangs off. Null means it has not been placed in
        # the topology yet, which existing rows start as.
        ('parent_id', 'INTEGER REFERENCES devices(id) ON DELETE SET NULL'),
    ),
    'ping_results': (
        ('jitter_ms', 'REAL'),
        ('packet_loss', 'REAL'),
    ),
    'status_changes': (
        ('duration_seconds', 'INTEGER'),
    ),
    'notifications': (
        ('escalated', 'INTEGER NOT NULL DEFAULT 0'),
    ),
    'users': (
        ('phone_number', 'TEXT'),
        ('mfa_enabled', 'INTEGER NOT NULL DEFAULT 1'),
        ('mfa_secret', "TEXT NOT NULL DEFAULT ''"),
        ('backup_code_hash', 'TEXT'),
        ('role', "TEXT NOT NULL DEFAULT 'operator'"),
        # Existing accounts stay usable; only new self-registrations start inactive.
        ('is_active', 'INTEGER NOT NULL DEFAULT 1'),
    ),
}

_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)'
    r'(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'
)


def normalize_target(value: str) -> str:
    """Return a canonical ping target, or raise ValueError.

    Accepts IPv4, IPv6 and DNS hostnames — devices on DHCP or reachable only by
    name were previously impossible to monitor.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Address must be a non-empty string")

    candidate = value.strip().rstrip('.')
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass

    # An all-numeric name like '999.1.1.1' satisfies the hostname grammar but is a
    # mistyped address, not a host. Rejecting it here stops the device being
    # created and then sitting permanently at 'unknown' with no explanation.
    labels = candidate.split('.')
    if len(labels) > 1 and all(label.isdigit() for label in labels):
        raise ValueError(f"'{value}' is not a valid IP address")

    if _HOSTNAME_RE.match(candidate):
        return candidate.lower()

    raise ValueError(f"'{value}' is not a valid IP address or hostname")


@dataclass
class Device:
    id: int
    name: str
    ip_address: str
    group_name: Optional[str]
    interval: int
    timeout: int
    status: str
    last_latency_ms: Optional[float]
    last_seen_at: Optional[str]
    created_at: str
    updated_at: str
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    uptime_percentage: Optional[float] = None
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    device_type: str = 'other'
    parent_id: Optional[int] = None


class DeviceManager:
    """Persist devices and monitor state in SQLite."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            # Not beside the module: in a packaged build that is inside the bundle,
            # which is read-only when installed and discarded on exit.
            db_path = paths.data_file("ping_monitor.sqlite3")
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()
        self.ensure_default_admin()

    def initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    ip_address TEXT NOT NULL,
                    group_name TEXT,
                    interval INTEGER NOT NULL DEFAULT 5,
                    timeout INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    last_latency_ms REAL,
                    min_latency_ms REAL,
                    max_latency_ms REAL,
                    avg_latency_ms REAL,
                    uptime_percentage REAL DEFAULT 100.0,
                    total_requests INTEGER DEFAULT 0,
                    successful_requests INTEGER DEFAULT 0,
                    failed_requests INTEGER DEFAULT 0,
                    device_type TEXT NOT NULL DEFAULT 'other',
                    parent_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
                    last_seen_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ping_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms REAL,
                    jitter_ms REAL,
                    packet_loss REAL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS status_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    duration_seconds INTEGER,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    is_acknowledged INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    message TEXT NOT NULL,
                    related_device_id INTEGER,
                    severity TEXT NOT NULL DEFAULT 'info',
                    is_acknowledged INTEGER DEFAULT 0,
                    escalated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    FOREIGN KEY(related_device_id) REFERENCES devices(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS device_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    color TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    phone_number TEXT,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'operator',
                    mfa_enabled INTEGER NOT NULL DEFAULT 1,
                    mfa_secret TEXT NOT NULL,
                    backup_code_hash TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    command TEXT NOT NULL,
                    cwd TEXT,
                    exit_code INTEGER,
                    duration_ms INTEGER,
                    ran_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._migrate_schema(conn)
            # Create indices for better query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_status ON devices(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ping_device_time ON ping_results(device_id, recorded_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_changes_device ON status_changes(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)")
            conn.commit()

    def ensure_default_admin(self) -> Optional[dict]:
        """Seed a first administrator, once, on an empty system.

        This deliberately does nothing when an admin already exists. The previous
        version reset the admin password and disabled MFA on every startup, which
        silently reverted real password changes and made MFA impossible to keep on.
        """
        if self.has_admin():
            return None

        username = os.environ.get('DEFAULT_ADMIN_USERNAME', 'admin')
        password = os.environ.get('DEFAULT_ADMIN_PASSWORD')
        generated = password is None
        if generated:
            password = secrets.token_urlsafe(12)

        existing = self.get_user_by_username(username)
        if existing is not None:
            # The name is taken by a non-admin account; promote it rather than
            # colliding, but never touch its password.
            with self._connect() as conn:
                conn.execute(
                    "UPDATE users SET role = 'admin', is_active = 1 WHERE username = ?",
                    (username,),
                )
                conn.commit()
            return self.get_user_by_username(username)

        user = self.create_user(
            username=username,
            password=password,
            display_name='Administrator',
            role='admin',
            mfa_enabled=False,
        )
        if generated:
            user['generated_password'] = password
            self._write_first_run_password(username, password)
        return user

    def first_run_password_file(self) -> Path:
        # Beside the database it belongs to, not the shared data directory: a
        # manager opened on another path would otherwise write its note over the
        # real installation's.
        return Path(self.db_path).parent / 'first-run-password.txt'

    def _write_first_run_password(self, username: str, password: str) -> None:
        """Leave the generated first password where the person installing can read it.

        A packaged application seeds its own administrator, and a password nobody is
        ever shown locks the owner out of their own install. This is written once,
        to the per-user data directory, and the file is deleted the moment that
        password is changed.
        """
        try:
            self.first_run_password_file().write_text(
                'Network Monitor — first run\n\n'
                f'  username: {username}\n'
                f'  password: {password}\n\n'
                'Sign in, change the password, and delete this file.\n'
                'It is removed automatically once the password is changed.\n',
                encoding='utf-8',
            )
        except OSError:
            pass

    def _clear_first_run_password(self) -> None:
        try:
            self.first_run_password_file().unlink(missing_ok=True)
        except OSError:
            pass

    def has_admin(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
        return row is not None

    def create_device(
        self,
        *,
        name: str,
        ip_address: str,
        interval: int = 5,
        timeout: int = 2,
        device_type: str = 'other',
        parent_id: Optional[int] = None,
    ) -> Device:
        self._validate_device_payload(name=name, ip_address=ip_address, interval=interval, timeout=timeout)
        name = name.strip()
        ip_address = normalize_target(ip_address)
        parent_id = self.validate_placement(device_type=device_type, parent_id=parent_id)

        now = self._timestamp()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM devices WHERE name = ?",
                (name,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Device with name '{name}' already exists")

            duplicate_ip = conn.execute(
                "SELECT name FROM devices WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            if duplicate_ip is not None:
                # get_device_by_ip returns the first match, so duplicates would
                # misattribute manual ping results to the wrong device.
                raise ValueError(
                    f"'{ip_address}' is already monitored as '{duplicate_ip['name']}'"
                )

            cursor = conn.execute(
                """
                INSERT INTO devices (
                    name, ip_address, group_name, interval, timeout, status,
                    device_type, parent_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?)
                """,
                (name, ip_address, None, interval, timeout, device_type, parent_id, now, now),
            )
            conn.commit()
            device_id = cursor.lastrowid
        return self.get_device(device_id)

    def get_device(self, device_id: int) -> Optional[Device]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_device(row)

    def list_devices(self) -> list[Device]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM devices ORDER BY id"
            ).fetchall()
        return [self._row_to_device(row) for row in rows]

    def record_ping_result(self, device_id: int, *, status: str, latency_ms: Optional[float] = None, packet_loss: int = 0) -> Device:
        """
        Record a ping result for a device.
        
        Args:
            device_id: Device ID
            status: Ping status ('online', 'offline', 'unknown')
            latency_ms: Response time in milliseconds
            packet_loss: Packet loss percentage (0-100)
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Status must be one of {', '.join(VALID_STATUSES)} — got {status!r}"
            )

        now = self._timestamp()
        device = self.get_device(device_id)
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status, last_seen_at, min_latency_ms, max_latency_ms, avg_latency_ms,"
                " total_requests, successful_requests, failed_requests"
                " FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"Device {device_id} does not exist")

            previous_status = current["status"]
            if previous_status != status:
                conn.execute(
                    "INSERT INTO status_changes (device_id, from_status, to_status, duration_seconds, recorded_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        device_id,
                        previous_status,
                        status,
                        self._seconds_in_previous_state(conn, device_id, now),
                        now,
                    ),
                )

                # Create a notification for status transitions (e.g., device came online)
                try:
                    if status == 'online':
                        title = f"Device Online: {device.name if device else device_id}"
                        message = f"{device.name if device else 'Device'} ({device.ip_address if device else ''}) is now online."
                        # insert notification
                        conn.execute(
                            "INSERT INTO notifications (title, message, related_device_id, severity, created_at) VALUES (?, ?, ?, ?, ?)",
                            (title, message, device_id, 'info', now),
                        )
                    elif status == 'offline':
                        title = f"Device Offline: {device.name if device else device_id}"
                        message = f"{device.name if device else 'Device'} ({device.ip_address if device else ''}) is now offline."
                        conn.execute(
                            "INSERT INTO notifications (title, message, related_device_id, severity, created_at) VALUES (?, ?, ?, ?, ?)",
                            (title, message, device_id, 'critical', now),
                        )
                except Exception:
                    # Don't let notification failures block recording ping results
                    pass

            # A failed ping has no latency. Storing 0.0 made outages render as an
            # exceptionally fast response on the latency chart.
            measured = float(latency_ms) if latency_ms is not None else None
            succeeded = status == 'online'

            conn.execute(
                "INSERT INTO ping_results (device_id, status, latency_ms, jitter_ms, packet_loss, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, status, measured, None, packet_loss, now),
            )

            total = int(current["total_requests"] or 0) + 1
            successful = int(current["successful_requests"] or 0) + (1 if succeeded else 0)
            failed = total - successful

            minimum = self._optional_float(current["min_latency_ms"])
            maximum = self._optional_float(current["max_latency_ms"])
            average = self._optional_float(current["avg_latency_ms"])

            if measured is not None:
                minimum = measured if minimum is None else min(minimum, measured)
                maximum = measured if maximum is None else max(maximum, measured)
                # Running mean over successful pings only.
                previous_successes = successful - 1
                if average is None or previous_successes <= 0:
                    average = measured
                else:
                    average = ((average * previous_successes) + measured) / successful

            uptime = round(successful / total * 100, 2) if total else 100.0

            # "Last seen" means the last time it actually answered.
            last_seen = now if succeeded else current["last_seen_at"]

            conn.execute(
                """
                UPDATE devices SET
                    status = ?, last_latency_ms = ?, last_seen_at = ?, updated_at = ?,
                    min_latency_ms = ?, max_latency_ms = ?, avg_latency_ms = ?,
                    uptime_percentage = ?, total_requests = ?, successful_requests = ?, failed_requests = ?
                WHERE id = ?
                """,
                (
                    status, measured, last_seen, now,
                    minimum, maximum, round(average, 2) if average is not None else None,
                    uptime, total, successful, failed,
                    device_id,
                ),
            )
            conn.commit()

        return self.get_device(device_id)

    def _seconds_in_previous_state(self, conn: sqlite3.Connection, device_id: int, now: str) -> Optional[int]:
        """How long the device held the status it is leaving.

        Measured from the last recorded transition, or from device creation if this
        is the first one. Without this the timeline cannot answer 'how long was it
        down', which is usually the first question asked in an incident.
        """
        row = conn.execute(
            "SELECT recorded_at FROM status_changes WHERE device_id = ? ORDER BY id DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT created_at AS recorded_at FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
        if row is None or not row["recorded_at"]:
            return None
        try:
            started = datetime.fromisoformat(row["recorded_at"])
            ended = datetime.fromisoformat(now)
        except ValueError:
            return None
        return max(0, int((ended - started).total_seconds()))
    
    def record_status_change(self, device_id: int, from_status: str, to_status: str) -> None:
        """
        Record a status change for a device.
        
        Args:
            device_id: Device ID
            from_status: Previous status
            to_status: New status
        """
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO status_changes (device_id, from_status, to_status, recorded_at) VALUES (?, ?, ?, ?)",
                (device_id, from_status, to_status, now),
            )

    def get_devices(self) -> list[Device]:
        """Get all devices (alias for list_devices)."""
        return self.list_devices()

    def get_device_by_id(self, device_id: int) -> Optional[Device]:
        """Get a device by ID (alias for get_device)."""
        return self.get_device(device_id)

    def get_user_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return int(row[0] if row else 0)

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    def get_device_by_ip(self, ip_address: str) -> Optional[Device]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
        return self._row_to_device(row) if row else None
    def verify_mfa_code(self, user: dict, code: str) -> bool:
        if not user or not code:
            return False

        try:
            totp = pyotp.TOTP(user['mfa_secret'])
            return bool(totp.verify(code, valid_window=1))
        except Exception:
            return False

    def verify_backup_code(self, user: dict, code: str) -> bool:
        if not user or not code or not user.get('backup_code_hash'):
            return False
        try:
            return check_password_hash(user['backup_code_hash'], code)
        except Exception:
            return False

    def clear_backup_code(self, user_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET backup_code_hash = NULL WHERE id = ?",
                (user_id,),
            )
            conn.commit()

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        role: str = 'operator',
        mfa_enabled: bool = True,
        is_active: bool = True,
    ) -> dict:
        self._validate_user_payload(username=username, password=password, role=role)
        username = username.strip()

        if self.get_user_count() == 0:
            role = 'admin'
            is_active = True

        now = self._timestamp()
        secret = pyotp.random_base32()
        backup_code = secrets.token_urlsafe(6)
        password_hash = generate_password_hash(password)
        backup_hash = generate_password_hash(backup_code)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"User '{username}' already exists")

            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, display_name, phone_number, password_hash, role, mfa_enabled,
                    mfa_secret, backup_code_hash, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, display_name, phone_number, password_hash, role, int(mfa_enabled),
                 secret, backup_hash, int(is_active), now),
            )
            conn.commit()
            user_id = cursor.lastrowid

        # Create a notification for admin about new user registration
        try:
            now = self._timestamp()
            with self._connect() as conn2:
                conn2.execute(
                    "INSERT INTO notifications (title, message, severity, created_at) VALUES (?, ?, ?, ?)",
                    ("New User Registered", f"User '{username}' registered with role '{role}'", 'info', now),
                )
                conn2.commit()
        except Exception:
            pass

        return {
            'id': user_id,
            'username': username,
            'display_name': display_name,
            'phone_number': phone_number,
            'role': role,
            'mfa_enabled': mfa_enabled,
            'is_active': is_active,
            'mfa_secret': secret,
            'backup_code': backup_code,
            'created_at': now,
        }

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, display_name, phone_number, role, mfa_enabled,"
                " is_active, created_at, last_login_at FROM users ORDER BY username"
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- topology

    def validate_placement(self, *, device_type: str, parent_id: Optional[int],
                           device_id: Optional[int] = None) -> Optional[int]:
        """Check where a device may sit in the network, and return its uplink.

        A network drawn from an unordered list of addresses is not a network. An
        endpoint has to plug into something that forwards traffic, and that uplink
        is what lets a failure be attributed to one point instead of forty.
        """
        if device_type not in DEVICE_TYPES:
            raise ValueError(
                f"Device type must be one of: {', '.join(sorted(DEVICE_TYPES))}"
            )

        is_infrastructure = DEVICE_TYPES[device_type]['infrastructure']

        if parent_id is None:
            if is_infrastructure:
                return None  # a router or switch may sit at the top of the network
            available = self.list_infrastructure_devices()
            if not available:
                raise ValueError(
                    f"Add a switch or router first — a {DEVICE_TYPES[device_type]['label'].lower()} "
                    "has to plug into something before it can appear on the network map"
                )
            raise ValueError(
                f"Choose the switch or router this {DEVICE_TYPES[device_type]['label'].lower()} "
                "connects to"
            )

        parent = self.get_device(parent_id)
        if parent is None:
            raise ValueError(f"Uplink device {parent_id} does not exist")
        if not DEVICE_TYPES.get(parent.device_type, {}).get('infrastructure'):
            raise ValueError(
                f"'{parent.name}' is a {DEVICE_TYPES.get(parent.device_type, {}).get('label', 'device')} "
                "and cannot have devices connected to it. Choose a router, switch, "
                "firewall or access point."
            )
        if device_id is not None:
            if parent_id == device_id:
                raise ValueError("A device cannot be its own uplink")
            # Walking up must terminate; a cycle would hang every traversal.
            seen = {device_id}
            cursor = parent
            while cursor is not None:
                if cursor.id in seen:
                    raise ValueError(
                        f"'{parent.name}' sits below this device — that would create a loop"
                    )
                seen.add(cursor.id)
                cursor = self.get_device(cursor.parent_id) if cursor.parent_id else None

        return parent_id

    def list_infrastructure_devices(self) -> list[Device]:
        """Devices other devices can be connected to."""
        return [
            d for d in self.list_devices()
            if DEVICE_TYPES.get(d.device_type, {}).get('infrastructure')
        ]

    def get_children(self, device_id: int) -> list[Device]:
        return [d for d in self.list_devices() if d.parent_id == device_id]

    def get_descendants(self, device_id: int) -> list[Device]:
        """Everything that reaches the network through this device."""
        devices = self.list_devices()
        by_parent: dict[Optional[int], list[Device]] = {}
        for device in devices:
            by_parent.setdefault(device.parent_id, []).append(device)

        found, queue, seen = [], [device_id], {device_id}
        while queue:
            for child in by_parent.get(queue.pop(), []):
                if child.id in seen:
                    continue
                seen.add(child.id)
                found.append(child)
                queue.append(child.id)
        return found

    def set_device_parent(self, device_id: int, parent_id: Optional[int]) -> Device:
        device = self.get_device(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} does not exist")

        parent_id = self.validate_placement(
            device_type=device.device_type, parent_id=parent_id, device_id=device_id
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET parent_id = ?, updated_at = ? WHERE id = ?",
                (parent_id, self._timestamp(), device_id),
            )
            conn.commit()
        return self.get_device(device_id)

    def _ancestors(self, device: Device, by_id: dict[int, Device]) -> list[Device]:
        chain, seen = [], {device.id}
        cursor = by_id.get(device.parent_id) if device.parent_id else None
        while cursor is not None and cursor.id not in seen:
            chain.append(cursor)
            seen.add(cursor.id)
            cursor = by_id.get(cursor.parent_id) if cursor.parent_id else None
        return chain

    def find_fault_domain(self, device_id: int) -> Optional[Device]:
        """The highest device that is down on the path to this one.

        When an access switch fails, every device behind it also fails to answer.
        This is the one that actually needs attention — the rest are consequences.
        """
        by_id = {d.id: d for d in self.list_devices()}
        device = by_id.get(device_id)
        if device is None:
            return None

        culprit = None
        for ancestor in self._ancestors(device, by_id):
            if ancestor.status == 'offline':
                culprit = ancestor  # keep climbing; the highest failure wins
        return culprit

    def get_topology(self) -> dict:
        """The network as a tree, with each device's effective reachability.

        `derived_status` separates a device that is genuinely down from one that
        simply sits behind something that is down.
        """
        devices = self.list_devices()
        by_id = {d.id: d for d in devices}

        nodes = []
        for device in devices:
            ancestors = self._ancestors(device, by_id)
            blocked_by = None
            for ancestor in ancestors:
                if ancestor.status == 'offline':
                    blocked_by = ancestor

            if blocked_by is not None and device.status != 'online':
                derived = 'unreachable'
            else:
                derived = device.status

            nodes.append({
                'id': device.id,
                'name': device.name,
                'ip_address': device.ip_address,
                'device_type': device.device_type,
                'type_label': DEVICE_TYPES.get(device.device_type, {}).get('label', 'Device'),
                'is_infrastructure': bool(
                    DEVICE_TYPES.get(device.device_type, {}).get('infrastructure')
                ),
                'parent_id': device.parent_id,
                'group_name': device.group_name,
                'status': device.status,
                'derived_status': derived,
                'blocked_by': (
                    {'id': blocked_by.id, 'name': blocked_by.name} if blocked_by else None
                ),
                'last_latency_ms': device.last_latency_ms,
                'uptime_percentage': device.uptime_percentage,
                'depth': len(ancestors),
                'child_count': sum(1 for d in devices if d.parent_id == device.id),
            })

        # A failing switch is one fault, not one per device behind it.
        incidents = []
        for node in nodes:
            if node['status'] != 'offline' or node['derived_status'] == 'unreachable':
                continue
            behind = sum(
                1 for other in nodes
                if other['blocked_by'] and other['blocked_by']['id'] == node['id']
            )
            incidents.append({
                'device_id': node['id'],
                'device_name': node['name'],
                'ip_address': node['ip_address'],
                'type_label': node['type_label'],
                'devices_affected': behind,
            })
        incidents.sort(key=lambda i: i['devices_affected'], reverse=True)

        return {
            'nodes': nodes,
            'roots': [n['id'] for n in nodes if n['parent_id'] is None],
            'unplaced': [n['id'] for n in nodes
                         if n['parent_id'] is None and not n['is_infrastructure']],
            'incidents': incidents,
            'device_types': [
                {'value': key, 'label': meta['label'], 'infrastructure': meta['infrastructure']}
                for key, meta in DEVICE_TYPES.items()
            ],
        }

    def record_command(self, *, username: Optional[str], command: str, cwd: str,
                       exit_code: int, duration_ms: int) -> None:
        """Keep a record of what was run.

        A tool several engineers share has to be able to answer 'who ran what',
        and an audit trail is cheap.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO command_log (username, command, cwd, exit_code, duration_ms, ran_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (username, command, cwd, exit_code, duration_ms, self._timestamp()),
            )
            conn.commit()

    def get_command_log(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM command_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Persist a system-wide setting.

        These used to live in the Flask session, which made them per-browser: not
        shared between administrators and lost at logout.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, self._timestamp()),
            )
            conn.commit()

    def change_password(self, user_id: int, *, current_password: str, new_password: str) -> None:
        """Change a user's own password, proving they know the current one."""
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError("Account not found")
        if not check_password_hash(user['password_hash'], current_password):
            raise ValueError("Current password is incorrect")
        if len(new_password or '') < 12:
            raise ValueError("New password must be at least 12 characters")
        if new_password == current_password:
            raise ValueError("New password must be different from the current one")

        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), user_id),
            )
            conn.commit()

        # The seeded password is no longer valid, so the note holding it is waste.
        self._clear_first_run_password()

        self._notify(
            "Password Changed",
            f"Password updated for '{user['username']}'.",
            severity='info',
        )

    def uses_default_password(self, username: str = 'admin') -> bool:
        """Whether an account still has the password published in this repository.

        The seeder no longer resets it, but an installation created before that fix
        still carries it, and the value is public.
        """
        user = self.get_user_by_username(username)
        if user is None:
            return False
        return check_password_hash(user['password_hash'], 'Admin12345!')

    def count_admins(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()
        return int(row[0] if row else 0)

    def set_user_active(self, user_id: int, is_active: bool) -> dict:
        """Approve or suspend an account.

        Self-registered accounts start inactive so an administrator decides who
        gets to see the network inventory.
        """
        user = self.get_user_by_id(user_id)
        if user is None:
            raise ValueError(f"User {user_id} does not exist")

        if not is_active and user['role'] == 'admin' and self.count_admins() <= 1:
            raise ValueError("Cannot suspend the last administrator")

        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET is_active = ? WHERE id = ?",
                (1 if is_active else 0, user_id),
            )
            conn.commit()

        self._notify(
            "Account Approved" if is_active else "Account Suspended",
            f"User '{user['username']}' was {'approved' if is_active else 'suspended'}.",
            severity='info',
        )
        return self.get_user_by_id(user_id)

    def _notify(self, title: str, message: str, severity: str = 'info',
                device_id: Optional[int] = None) -> None:
        """Best-effort notification write; never blocks the caller's real work."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO notifications (title, message, related_device_id, severity, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (title, message, device_id, severity, self._timestamp()),
                )
                conn.commit()
        except Exception:
            pass

    def update_user_role(self, user_id: int, role: str) -> dict:
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}")

        existing = self.get_user_by_id(user_id)
        if existing is None:
            raise ValueError(f"User {user_id} does not exist")

        if existing['role'] == 'admin' and role != 'admin' and self.count_admins() <= 1:
            raise ValueError(
                "Cannot demote the last administrator — promote someone else first"
            )

        now = self._timestamp()
        with self._connect() as conn:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            conn.commit()
        user = self.get_user_by_id(user_id)
        try:
            # notify about role change
            with self._connect() as conn2:
                conn2.execute(
                    "INSERT INTO notifications (title, message, severity, created_at) VALUES (?, ?, ?, ?)",
                    ("User Role Updated", f"User '{user.get('username')}' role set to '{role}'", 'info', now),
                )
                conn2.commit()
        except Exception:
            pass
        return user

    def get_notifications(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_notification(self, notification_id: int) -> None:
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute("UPDATE notifications SET is_acknowledged = 1, acknowledged_at = ? WHERE id = ?", (now, notification_id))
            conn.commit()

    def escalate_unacknowledged_notifications(self, older_than_minutes: int = 5) -> int:
        """
        Find critical, unacknowledged notifications older than `older_than_minutes` and create escalation notices.
        Marks the original notification as escalated to avoid duplicate escalations.

        Returns the number of escalations created.
        """
        now = self._timestamp()
        escalated_count = 0
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)).replace(microsecond=0).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, message, related_device_id, created_at FROM notifications WHERE severity = 'critical' AND is_acknowledged = 0 AND (escalated IS NULL OR escalated = 0) AND created_at < ?",
                (cutoff,)
            ).fetchall()
            for row in rows:
                nid = row['id']
                title = f"Escalation: {row['title'] or 'Critical Notification'}"
                message = f"Escalation triggered for: {row['message']}"
                related = row['related_device_id']
                try:
                    # The escalation notice is itself critical and unacknowledged, so
                    # without escalated = 1 the next pass escalates the escalation,
                    # and the message nests one level deeper every minute.
                    conn.execute(
                        "INSERT INTO notifications (title, message, related_device_id, severity, escalated, created_at)"
                        " VALUES (?, ?, ?, ?, 1, ?)",
                        (title, message, related, 'critical', now),
                    )
                    conn.execute("UPDATE notifications SET escalated = 1 WHERE id = ?", (nid,))
                    escalated_count += 1
                except Exception:
                    # continue on error
                    continue
            conn.commit()
        return escalated_count

    def update_user_last_login(self, user_id: int) -> None:
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (now, user_id),
            )
            conn.commit()

    # Sentinel so a caller can distinguish "leave this alone" from "set it to None".
    _UNSET = object()

    def update_device(
        self,
        device_id: int,
        *,
        name: Optional[str] = None,
        ip_address: Optional[str] = None,
        interval: Optional[int] = None,
        timeout: Optional[int] = None,
        device_type: Optional[str] = None,
        parent_id=_UNSET,
        group_name=_UNSET,
    ) -> Device:
        """Update device settings, including its place in the topology.

        One call so the edit form saves atomically instead of firing a request per
        field and leaving the device half-updated when one of them fails.
        """
        device = self.get_device(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} does not exist")

        name = (name or device.name).strip()
        interval = interval or device.interval
        timeout = timeout or device.timeout
        device_type = device_type or device.device_type
        address = normalize_target(ip_address) if ip_address else device.ip_address

        self._validate_device_payload(
            name=name, ip_address=address, interval=interval, timeout=timeout
        )

        uplink = device.parent_id if parent_id is self._UNSET else parent_id
        uplink = self.validate_placement(
            device_type=device_type, parent_id=uplink, device_id=device_id
        )

        now = self._timestamp()
        with self._connect() as conn:
            clash = conn.execute(
                "SELECT 1 FROM devices WHERE name = ? AND id != ?", (name, device_id)
            ).fetchone()
            if clash is not None:
                raise ValueError(f"Device with name '{name}' already exists")

            if address != device.ip_address:
                duplicate = conn.execute(
                    "SELECT name FROM devices WHERE ip_address = ? AND id != ?",
                    (address, device_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(
                        f"'{address}' is already monitored as '{duplicate['name']}'"
                    )

            group = device.group_name if group_name is self._UNSET else (group_name or None)

            conn.execute(
                "UPDATE devices SET name = ?, ip_address = ?, interval = ?, timeout = ?,"
                " device_type = ?, parent_id = ?, group_name = ?, updated_at = ?"
                " WHERE id = ?",
                (name, address, interval, timeout, device_type, uplink, group, now, device_id),
            )
            conn.commit()

        return self.get_device(device_id)

    def delete_device(self, device_id: int) -> None:
        """Delete a device and all associated ping results."""
        children = self.get_children(device_id)
        if children:
            names = ', '.join(sorted(c.name for c in children)[:4])
            more = f" and {len(children) - 4} more" if len(children) > 4 else ''
            raise ValueError(
                f"{len(children)} device(s) connect through this one ({names}{more}). "
                "Move them to another uplink first."
            )
        with self._connect() as conn:
            conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
            conn.commit()

    @dataclass
    class PingResult:
        timestamp: str
        latency_ms: float
        status: str

    def get_ping_history(self, device_id: int, limit: int = 100) -> list[PingResult]:
        """Get ping history for a device."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT recorded_at, latency_ms, status FROM ping_results WHERE device_id = ? ORDER BY recorded_at DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()

        return [
            self.PingResult(
                timestamp=row["recorded_at"],
                latency_ms=float(row["latency_ms"] or 0.0),
                status=row["status"],
            )
            for row in reversed(rows)  # Reverse to get chronological order
        ]

    def get_device_statistics(self, device_id: int, hours: int = 24) -> dict:
        """Get comprehensive device statistics."""
        with self._connect() as conn:
            device = self.get_device(device_id)
            if not device:
                return {}

            # The cutoff is built in the same format the app writes timestamps in.
            # Comparing against SQLite's datetime('now', ...) compared
            # '2026-08-18T19:43:23+00:00' with '2026-08-17 19:43:23'; the strings
            # diverge at the 'T'/space, so a 24-hour window silently returned ~48
            # hours of rows.
            cutoff = self._cutoff(hours)

            stats = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN status='online' THEN 1 ELSE 0 END) as online_count"
                " FROM ping_results WHERE device_id = ? AND recorded_at > ?",
                (device_id, cutoff),
            ).fetchone()

            latency_stats = conn.execute(
                "SELECT MIN(latency_ms) as min, MAX(latency_ms) as max, AVG(latency_ms) as avg"
                " FROM ping_results WHERE device_id = ? AND latency_ms IS NOT NULL AND recorded_at > ?",
                (device_id, cutoff),
            ).fetchone()

            status_changes = conn.execute(
                "SELECT COUNT(*) as count FROM status_changes WHERE device_id = ? AND recorded_at > ?",
                (device_id, cutoff),
            ).fetchone()

        total = stats["total"] or 0
        online = stats["online_count"] or 0
        uptime = (online / total * 100) if total > 0 else 0

        return {
            'device_id': device_id,
            'device_name': device.name,
            'total_pings': total,
            'successful_pings': online,
            'failed_pings': total - online,
            'uptime_percentage': round(uptime, 2),
            # None means "no successful ping in this window" — distinct from 0 ms.
            'min_latency_ms': self._optional_float(latency_stats["min"]),
            'max_latency_ms': self._optional_float(latency_stats["max"]),
            'avg_latency_ms': (
                round(float(latency_stats["avg"]), 2)
                if latency_stats["avg"] is not None else None
            ),
            'status_changes': status_changes["count"] or 0,
            'current_status': device.status,
            'last_seen': device.last_seen_at,
            'hours': hours
        }

    def get_status_timeline(self, device_id: int, limit: int = 50) -> list[dict]:
        """Get status change timeline for a device."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT from_status, to_status, duration_seconds, recorded_at 
                   FROM status_changes 
                   WHERE device_id = ? 
                   ORDER BY recorded_at DESC 
                   LIMIT ?""",
                (device_id, limit),
            ).fetchall()

        return [
            {
                'from_status': row["from_status"],
                'to_status': row["to_status"],
                'duration_seconds': row["duration_seconds"],
                'recorded_at': row["recorded_at"],
                'is_transition': row["from_status"] != row["to_status"]
            }
            for row in reversed(rows)
        ]

    def create_alert(self, device_id: int, alert_type: str, severity: str, message: str) -> dict:
        """Create an alert for a device."""
        now = self._timestamp()
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO alerts (device_id, alert_type, severity, message, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (device_id, alert_type, severity, message, now),
            )
            conn.commit()
            alert_id = cursor.lastrowid

        return {
            'id': alert_id,
            'device_id': device_id,
            'alert_type': alert_type,
            'severity': severity,
            'message': message,
            'created_at': now,
            'acknowledged': False
        }

    def get_alerts(self, device_id: Optional[int] = None, unacknowledged_only: bool = False) -> list[dict]:
        """Get alerts for a device or all devices."""
        with self._connect() as conn:
            if device_id:
                query = "SELECT * FROM alerts WHERE device_id = ?"
                params = (device_id,)
            else:
                query = "SELECT * FROM alerts"
                params = ()

            if unacknowledged_only:
                query += " AND is_acknowledged = 0" if device_id else " WHERE is_acknowledged = 0"

            query += " ORDER BY created_at DESC LIMIT 100"
            rows = conn.execute(query, params).fetchall()

        return [dict(row) for row in rows]

    def acknowledge_alert(self, alert_id: int) -> None:
        """Mark an alert as acknowledged."""
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE alerts SET is_acknowledged = 1, acknowledged_at = ? WHERE id = ?",
                (now, alert_id),
            )
            conn.commit()

    def create_device_group(self, name: str, description: str = "", color: str = "#667eea") -> dict:
        """Create a device group."""
        now = self._timestamp()
        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO device_groups (name, description, color, created_at) VALUES (?, ?, ?, ?)",
                    (name, description, color, now),
                )
                conn.commit()
                group_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError(f"Group '{name}' already exists")

        return {
            'id': group_id,
            'name': name,
            'description': description,
            'color': color,
            'created_at': now
        }

    def get_device_groups(self) -> list[dict]:
        """Get all device groups."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM device_groups ORDER BY name").fetchall()

        return [dict(row) for row in rows]

    def assign_device_to_group(self, device_id: int, group_name: str) -> Device:
        """Assign a device to a group."""
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET group_name = ?, updated_at = ? WHERE id = ?",
                (group_name, now, device_id),
            )
            conn.commit()

        return self.get_device(device_id)

    def get_network_summary(self) -> dict:
        """Get network-wide summary statistics."""
        with self._connect() as conn:
            devices = conn.execute("SELECT * FROM devices").fetchall()
            device_list = [dict(d) for d in devices]

        total = len(device_list)
        online = sum(1 for d in device_list if d['status'] == 'online')
        offline = sum(1 for d in device_list if d['status'] == 'offline')
        # Previously offline was computed as total - online, which folded unknown
        # devices in and then counted them again as unknown: the three figures
        # summed to more than the device count.
        unknown = total - online - offline

        # Average over devices that actually answered. Including offline devices as
        # 0 ms dragged the figure toward zero exactly as the network degraded.
        latencies = [
            d['last_latency_ms'] for d in device_list
            if d['status'] == 'online' and d.get('last_latency_ms') is not None
        ]
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else None

        # Devices that have never been polled are not evidence of ill health.
        assessed = online + offline

        return {
            'total_devices': total,
            'online_devices': online,
            'offline_devices': offline,
            'unknown_devices': unknown,
            'average_latency_ms': avg_latency,
            'network_health_percentage': round(online / assessed * 100, 2) if assessed else None,
            'devices_by_group': self._group_devices_by_category(device_list)
        }

    def _group_devices_by_category(self, devices: list) -> dict:
        """Group devices by category."""
        groups = {}
        for device in devices:
            group = device.get('group_name') or 'Ungrouped'
            if group not in groups:
                groups[group] = []
            groups[group].append({
                'id': device['id'],
                'name': device['name'],
                'status': device['status'],
                'ip_address': device['ip_address']
            })
        return groups

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # SQLite ignores ON DELETE CASCADE unless this is set, per connection.
        # Without it, deleting a device orphans its history forever.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _optional_float(value) -> Optional[float]:
        return None if value is None else float(value)

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        polled = int(row["total_requests"] or 0)
        # The column defaults to 100.0, which would report a device that has never
        # been polled as perfectly healthy. No measurement means no percentage.
        uptime = self._optional_float(row["uptime_percentage"]) if polled else None
        return Device(
            id=row["id"],
            name=row["name"],
            ip_address=row["ip_address"],
            group_name=row["group_name"],
            interval=row["interval"],
            timeout=row["timeout"],
            status=row["status"],
            # None means "never measured" / "unreachable" — not 0 ms, which the
            # latency chart would draw as an exceptionally fast response.
            last_latency_ms=self._optional_float(row["last_latency_ms"]),
            last_seen_at=row["last_seen_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            min_latency_ms=self._optional_float(row["min_latency_ms"]),
            max_latency_ms=self._optional_float(row["max_latency_ms"]),
            avg_latency_ms=self._optional_float(row["avg_latency_ms"]),
            uptime_percentage=uptime,
            total_requests=polled,
            successful_requests=int(row["successful_requests"] or 0),
            failed_requests=int(row["failed_requests"] or 0),
            device_type=row["device_type"] or 'other',
            parent_id=row["parent_id"],
        )

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Add any column an older database is missing.

        Driven by SCHEMA_ADDITIONS so a new column is declared in exactly one place
        and every existing installation picks it up on next start.
        """
        for table, columns in SCHEMA_ADDITIONS.items():
            existing = {row['name'] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table not created yet; CREATE TABLE above handles it
            for column, definition in columns:
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _cutoff(hours: int) -> str:
        """A timestamp `hours` in the past, in the same format rows are stored in."""
        moment = datetime.now(timezone.utc) - timedelta(hours=hours)
        return moment.replace(microsecond=0).isoformat()

    @staticmethod
    def _validate_device_payload(*, name: str, ip_address: str, interval: int, timeout: int) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Device name must be a non-empty string")
        # Rejects markup, shell fragments and typos at the door. Anything that gets
        # past here is rendered in the dashboard and passed to the ping subprocess.
        normalize_target(ip_address)
        if not isinstance(interval, int) or interval <= 0:
            raise ValueError("Interval must be a positive integer")
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValueError("Timeout must be a positive integer")

    @staticmethod
    def _validate_user_payload(*, username: str, password: str, role: str) -> None:
        if not isinstance(username, str) or not username.strip():
            raise ValueError("Username must be a non-empty string")
        if not isinstance(password, str) or len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if role not in ('admin', 'operator', 'network_engineer', 'viewer'):
            raise ValueError("Role must be one of: 'admin', 'operator', 'network_engineer', 'viewer'")
