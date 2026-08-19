"""
Ping Service - Real-time device ping monitoring and connectivity tracking.

This module provides the core ping functionality:
- Performs actual ICMP ping to devices
- Tracks response time (latency) in milliseconds
- Records connectivity status (online/offline)
- Handles timeouts and error conditions
- Maintains connectivity history
"""

import subprocess
import platform
import re
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, Tuple, Optional

from .device_manager import normalize_target

logger = logging.getLogger(__name__)

# How many devices may be probed at once. A serial sweep meant one unreachable
# host delayed every device behind it in the list.
DEFAULT_WORKERS = 16


class PingService:
    """Service for pinging devices and tracking network connectivity."""
    
    # Platform-specific ping commands
    PING_COMMANDS = {
        'Windows': {'cmd': 'ping', 'args': '-n', 'timeout_arg': '-w'},  # milliseconds
        'Linux': {'cmd': 'ping', 'args': '-c', 'timeout_arg': '-W'},    # milliseconds
        'Darwin': {'cmd': 'ping', 'args': '-c', 'timeout_arg': '-W'},   # macOS
    }
    
    def __init__(self):
        """Initialize ping service."""
        self.platform = platform.system()
        self.is_running = False
        self.monitoring_thread = None
        self.last_results = {}
        # Lets stop_monitoring() interrupt the sleep instead of waiting it out.
        self._stop_event = threading.Event()
        
    def ping_device(self, ip_address: str, timeout: int = 2, count: int = 1) -> Dict:
        """
        Ping a single device and return detailed results.
        
        Args:
            ip_address: IP address to ping (e.g., '192.168.1.1')
            timeout: Timeout in seconds (default: 2)
            count: Number of ping attempts (default: 1)
            
        Returns:
            Dictionary with:
                - 'status': 'online' or 'offline'
                - 'latency_ms': Response time in milliseconds (None if offline)
                - 'packet_loss': Percentage of lost packets
                - 'timestamp': ISO format datetime
                - 'error': Error message if failed
        """
        
        try:
            # Validate IP address format
            if not self._is_valid_ip(ip_address):
                return {
                    'status': 'unknown',
                    'latency_ms': None,
                    'packet_loss': 100,
                    'timestamp': datetime.now().isoformat(),
                    'error': f'Invalid IP address format: {ip_address}'
                }
            
            # Get platform-specific ping command
            if self.platform not in self.PING_COMMANDS:
                return {
                    'status': 'unknown',
                    'latency_ms': None,
                    'packet_loss': 100,
                    'timestamp': datetime.now().isoformat(),
                    'error': f'Unsupported platform: {self.platform}'
                }
            
            cmd_info = self.PING_COMMANDS[self.platform]

            # The timeout flag takes different units per platform. Windows -w and
            # macOS -W are milliseconds; Linux -W is seconds. Passing milliseconds
            # to Linux asked for a 2000-second wait on every unreachable host.
            if self.platform == 'Linux':
                timeout_value = str(max(1, int(timeout)))
            else:
                timeout_value = str(int(timeout * 1000))

            cmd = [
                cmd_info['cmd'],
                cmd_info['args'], str(count),
                cmd_info['timeout_arg'], timeout_value,
                ip_address
            ]

            # Execute ping command
            start_time = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors='replace',
                timeout=timeout + 5  # Add buffer for command execution
            )
            elapsed_time = time.time() - start_time

            # Parse ping output
            return self._parse_ping_output(result.stdout, result.returncode, elapsed_time)
            
        except subprocess.TimeoutExpired:
            return {
                'status': 'offline',
                'latency_ms': None,
                'packet_loss': 100,
                'timestamp': datetime.now().isoformat(),
                'error': f'Ping timeout after {timeout} seconds'
            }
        except Exception as e:
            logger.error(f"Error pinging {ip_address}: {str(e)}")
            return {
                'status': 'unknown',
                'latency_ms': None,
                'packet_loss': 100,
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
    
    def _is_valid_ip(self, ip: str) -> bool:
        """Validate that a target is a usable IPv4/IPv6 address or hostname."""
        try:
            normalize_target(ip)
            return True
        except ValueError:
            return False

    def _parse_ping_output(self, output: str, returncode: int, elapsed: float) -> Dict:
        """Decide reachability from the ping output, not just the exit code.

        Windows `ping` exits 0 whenever any ICMP reply arrives — including a router
        answering "Destination host unreachable" or "TTL expired in transit" on
        behalf of a target that is completely down. Trusting the exit code alone
        drew dead devices green with 0% packet loss.
        """

        result = {
            'timestamp': datetime.now().isoformat(),
            'packet_loss': 0
        }

        lowered = output.lower()
        failure_markers = (
            'destination host unreachable',
            'destination net unreachable',
            'destination port unreachable',
            'destination unreachable',
            'ttl expired in transit',
            'request timed out',
            'request timeout',
            'no route to host',
            'network is unreachable',
            'host unreachable',
            'could not find host',
            'unknown host',
            'name or service not known',
            '100% packet loss',
            '100% loss',
        )
        rejected = any(marker in lowered for marker in failure_markers)

        latency = self._extract_latency(output)

        if returncode == 0 and not rejected:
            result['status'] = 'online'
            result['latency_ms'] = latency
            result['packet_loss'] = self._extract_packet_loss(output) or 0
        else:
            result['status'] = 'offline'
            result['latency_ms'] = None
            result['packet_loss'] = 100
            if rejected and returncode == 0:
                result['error'] = self._failure_reason(output)

        return result

    @staticmethod
    def _failure_reason(output: str) -> str:
        """The line the router actually sent back, for the alert message."""
        for line in output.splitlines():
            stripped = line.strip()
            if 'unreachable' in stripped.lower() or 'ttl expired' in stripped.lower():
                return stripped
        return 'Host did not respond'

    @staticmethod
    def _extract_packet_loss(output: str) -> Optional[int]:
        match = re.search(r'\((\d+)%\s*(?:packet\s*)?loss', output, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
    
    def _extract_latency(self, output: str) -> Optional[float]:
        """Extract latency value from ping output."""
        
        try:
            # Windows pattern: time=XXms
            windows_pattern = r'time[<=]+(\d+)ms'
            match = re.search(windows_pattern, output)
            if match:
                return float(match.group(1))
            
            # Linux/macOS pattern: time=XX.X ms
            unix_pattern = r'time=(\d+\.?\d*)\s*ms'
            match = re.search(unix_pattern, output)
            if match:
                return float(match.group(1))
            
            # Alternative pattern: min/avg/max/stddev
            avg_pattern = r'min/avg/max[/\w]*\s*=\s*[\d.]+/([\d.]+)'
            match = re.search(avg_pattern, output)
            if match:
                return float(match.group(1))
            
            return None
            
        except (AttributeError, ValueError):
            return None
    
    def start_monitoring(self, device_manager, interval: int = 5):
        """
        Start continuous device monitoring in background thread.
        
        Args:
            device_manager: DeviceManager instance for database operations
            interval: Ping interval in seconds (default: 5)
        """
        if self.is_running:
            logger.warning("Monitoring already running")
            return
        
        self.is_running = True
        self._stop_event.clear()
        self.monitoring_thread = threading.Thread(
            target=self._monitor_loop,
            args=(device_manager, interval),
            daemon=True
        )
        self.monitoring_thread.start()
        logger.info(f"Started device monitoring (default interval: {interval}s)")

    def stop_monitoring(self):
        """Stop the monitoring thread."""
        self.is_running = False
        self._stop_event.set()
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=10)
            if self.monitoring_thread.is_alive():
                logger.warning("Monitoring thread did not stop within 10s")
        logger.info("Stopped device monitoring")
    
    def _monitor_loop(self, device_manager, interval: int):
        """Poll every device on its own schedule, concurrently.

        Each device is probed when its own `interval` has elapsed since its last
        probe — the column was previously stored and shown in the UI but never
        read. Probes run in a thread pool, so one unreachable host no longer holds
        up every device behind it in the list.

        Args:
            device_manager: DeviceManager instance
            interval: Fallback interval for devices with none configured
        """
        due_at: dict[int, float] = {}

        with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS, thread_name_prefix='ping') as pool:
            while self.is_running:
                cycle_started = time.monotonic()
                try:
                    devices = device_manager.list_devices()
                    now = time.monotonic()

                    due = [d for d in devices if due_at.get(d.id, 0.0) <= now]
                    for device in due:
                        due_at[device.id] = now + max(1, device.interval or interval)

                    # Forget devices that have been deleted.
                    live_ids = {d.id for d in devices}
                    for stale in set(due_at) - live_ids:
                        due_at.pop(stale, None)

                    if due:
                        list(pool.map(
                            lambda d: self._probe_and_record(device_manager, d),
                            due,
                        ))

                except Exception as e:
                    logger.error(f"Error in monitoring loop: {str(e)}")

                # Wake often enough to honour the shortest configured interval,
                # but never spin.
                elapsed = time.monotonic() - cycle_started
                self._stop_event.wait(max(0.5, 1.0 - elapsed))

    def _probe_and_record(self, device_manager, device) -> None:
        """Ping one device, store the result, and alert on a status change."""
        if not self.is_running:
            return
        try:
            result = self.ping_device(
                device.ip_address,
                timeout=device.timeout,
                count=1
            )

            self.last_results[device.ip_address] = result

            device_manager.record_ping_result(
                device_id=device.id,
                status=result['status'],
                latency_ms=result['latency_ms'],
                packet_loss=result['packet_loss']
            )

            if device.status != result['status']:
                self._raise_status_alert(device_manager, device, result)
        except Exception as e:
            logger.error(f"Error probing {device.name} ({device.ip_address}): {e}")

    @staticmethod
    def _raise_status_alert(device_manager, device, result: Dict) -> None:
        """Alert on a status change, attributing failures to their real cause.

        A failed switch takes everything behind it with it. Raising a critical
        alert for each of those devices buries the one alert that matters, so a
        device that is only unreachable *through* a failed uplink is logged
        against that uplink instead of competing with it.
        """
        new_status = result['status']

        if new_status == 'offline':
            culprit = device_manager.find_fault_domain(device.id)
            if culprit is not None:
                device_manager.create_alert(
                    device_id=device.id,
                    alert_type='unreachable_via_uplink',
                    severity='info',
                    message=(
                        f'{device.name} is unreachable behind {culprit.name} '
                        f'({culprit.ip_address}) — not a separate fault'
                    )
                )
                return

            behind = len(device_manager.get_descendants(device.id))
            detail = f" ({result['error']})" if result.get('error') else ''
            impact = f' — {behind} device(s) behind it are now cut off' if behind else ''
            device_manager.create_alert(
                device_id=device.id,
                alert_type='status_change',
                severity='critical',
                message=f'{device.name} went offline{detail}{impact}'
            )
            return

        device_manager.create_alert(
            device_id=device.id,
            alert_type='status_change',
            severity='info',
            message=f'{device.name} changed from {device.status} to {new_status}'
        )
    
    def get_device_connectivity(self, ip_address: str) -> Dict:
        """
        Get current connectivity status for a device.
        
        Args:
            ip_address: IP address to check
            
        Returns:
            Last ping result for the device
        """
        if ip_address in self.last_results:
            return self.last_results[ip_address]
        
        # If no cached result, perform a quick ping
        result = self.ping_device(ip_address)
        self.last_results[ip_address] = result
        return result


# Global ping service instance
ping_service = PingService()
