from __future__ import annotations
import ipaddress
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pyotp
from werkzeug.security import check_password_hash, generate_password_hash


@dataclass
class Device:
    id: int
    name: str
    ip_address: str
    group_name: Optional[str]
    interval: int
    timeout: int
    status: str
    last_latency_ms: float
    last_seen_at: Optional[str]
    created_at: str
    updated_at: str


class DeviceManager:
    """Persist devices and monitor state in SQLite."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            db_path = str(Path(__file__).resolve().parent / "data" / "ping_monitor.sqlite3")
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()

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
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )
            self._migrate_user_schema(conn)
            # Create indices for better query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_device_status ON devices(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ping_device_time ON ping_results(device_id, recorded_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status_changes_device ON status_changes(device_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id)")
            conn.commit()

    def create_device(
        self,
        *,
        name: str,
        ip_address: str,
        interval: int = 5,
        timeout: int = 2,
    ) -> Device:
        self._validate_device_payload(name=name, ip_address=ip_address, interval=interval, timeout=timeout)

        now = self._timestamp()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM devices WHERE name = ?",
                (name,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"Device with name '{name}' already exists")

            cursor = conn.execute(
                """
                INSERT INTO devices (
                    name, ip_address, group_name, interval, timeout, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'unknown', ?, ?)
                """,
                (name, ip_address, None, interval, timeout, now, now),
            )
            conn.commit()
            device_id = cursor.lastrowid
        return self.get_device(device_id)

    def get_device(self, device_id: int) -> Optional[Device]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, name, ip_address, group_name, interval, timeout, status, last_latency_ms, last_seen_at, created_at, updated_at FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_device(row)

    def list_devices(self) -> list[Device]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, ip_address, group_name, interval, timeout, status, last_latency_ms, last_seen_at, created_at, updated_at FROM devices ORDER BY id"
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
        if not status or not status.strip():
            raise ValueError("Status must be a non-empty string")

        now = self._timestamp()
        with self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM devices WHERE id = ?",
                (device_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"Device {device_id} does not exist")

            previous_status = current[0]
            if previous_status != status:
                conn.execute(
                    "INSERT INTO status_changes (device_id, from_status, to_status, recorded_at) VALUES (?, ?, ?, ?)",
                    (device_id, previous_status, status, now),
                )

            normalized_latency = float(latency_ms) if latency_ms is not None else 0.0
            conn.execute(
                "INSERT INTO ping_results (device_id, status, latency_ms, jitter_ms, packet_loss, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, status, normalized_latency, 0, packet_loss, now),
            )
            conn.execute(
                "UPDATE devices SET status = ?, last_latency_ms = ?, last_seen_at = ?, updated_at = ? WHERE id = ?",
                (status, normalized_latency, now, now, device_id),
            )
            conn.commit()

        return self.get_device(device_id)
    
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
                "SELECT id, name, ip_address, group_name, interval, timeout, status, last_latency_ms, last_seen_at, created_at, updated_at FROM devices WHERE ip_address = ?",
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
    ) -> dict:
        self._validate_user_payload(username=username, password=password, role=role)

        if self.get_user_count() == 0:
            role = 'admin'

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
                    username, display_name, phone_number, password_hash, role, mfa_enabled, mfa_secret, backup_code_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, display_name, phone_number, password_hash, role, int(mfa_enabled), secret, backup_hash, now),
            )
            conn.commit()
            user_id = cursor.lastrowid

        return {
            'id': user_id,
            'username': username,
            'display_name': display_name,
            'phone_number': phone_number,
            'role': role,
            'mfa_enabled': mfa_enabled,
            'mfa_secret': secret,
            'backup_code': backup_code,
            'created_at': now,
        }

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, username, display_name, phone_number, role, mfa_enabled, created_at, last_login_at FROM users ORDER BY username").fetchall()
        return [dict(row) for row in rows]

    def update_user_last_login(self, user_id: int) -> None:
        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (now, user_id),
            )
            conn.commit()

    def update_device(
        self,
        device_id: int,
        *,
        name: Optional[str] = None,
        interval: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Device:
        """Update device settings."""
        device = self.get_device(device_id)
        if device is None:
            raise ValueError(f"Device {device_id} does not exist")

        # Use existing values if not provided
        name = name or device.name
        interval = interval or device.interval
        timeout = timeout or device.timeout

        self._validate_device_payload(name=name, ip_address=device.ip_address, interval=interval, timeout=timeout)

        now = self._timestamp()
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET name = ?, interval = ?, timeout = ?, updated_at = ? WHERE id = ?",
                (name, interval, timeout, now, device_id),
            )
            conn.commit()

        return self.get_device(device_id)

    def delete_device(self, device_id: int) -> None:
        """Delete a device and all associated ping results."""
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

            # Get stats from database
            stats = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN status='online' THEN 1 ELSE 0 END) as online_count FROM ping_results WHERE device_id = ? AND recorded_at > datetime('now', '-' || ? || ' hours')",
                (device_id, hours),
            ).fetchone()

            latency_stats = conn.execute(
                "SELECT MIN(latency_ms) as min, MAX(latency_ms) as max, AVG(latency_ms) as avg FROM ping_results WHERE device_id = ? AND latency_ms > 0 AND recorded_at > datetime('now', '-' || ? || ' hours')",
                (device_id, hours),
            ).fetchone()

            status_changes = conn.execute(
                "SELECT COUNT(*) as count FROM status_changes WHERE device_id = ? AND recorded_at > datetime('now', '-' || ? || ' hours')",
                (device_id, hours),
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
            'min_latency_ms': float(latency_stats["min"] or 0),
            'max_latency_ms': float(latency_stats["max"] or 0),
            'avg_latency_ms': round(float(latency_stats["avg"] or 0), 2),
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

        online = sum(1 for d in device_list if d['status'] == 'online')
        unknown = sum(1 for d in device_list if d['status'] == 'unknown')
        total = len(device_list)
        offline = total - online

        avg_latency = sum(d.get('last_latency_ms') or 0 for d in device_list) / total if total > 0 else 0

        return {
            'total_devices': total,
            'online_devices': online,
            'offline_devices': offline,
            'unknown_devices': unknown,
            'average_latency_ms': round(avg_latency, 2),
            'network_health_percentage': round(online / total * 100, 2) if total > 0 else 0,
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
        return conn

    def _row_to_device(self, row: sqlite3.Row) -> Device:
        return Device(
            id=row["id"],
            name=row["name"],
            ip_address=row["ip_address"],
            group_name=row["group_name"],
            interval=row["interval"],
            timeout=row["timeout"],
            status=row["status"],
            last_latency_ms=float(row["last_latency_ms"] or 0.0),
            last_seen_at=row["last_seen_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _migrate_user_schema(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(users)")
        columns = {row['name'] for row in cursor.fetchall()}
        if 'phone_number' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN phone_number TEXT")
        if 'mfa_enabled' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN mfa_enabled INTEGER NOT NULL DEFAULT 1")
        if 'mfa_secret' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN mfa_secret TEXT NOT NULL DEFAULT ''")
        if 'backup_code_hash' not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN backup_code_hash TEXT")
        conn.commit()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _validate_device_payload(*, name: str, ip_address: str, interval: int, timeout: int) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Device name must be a non-empty string")
        if not isinstance(ip_address, str) or not ip_address.strip():
            raise ValueError("IP address must be a non-empty string")
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
        if role not in ('admin', 'operator'):
            raise ValueError("Role must be 'admin' or 'operator'")
