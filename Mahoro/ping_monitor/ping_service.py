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
from datetime import datetime
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)


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
            
            # Build platform-specific ping command
            if self.platform == 'Windows':
                # Windows: ping -n {count} -w {timeout*1000} {ip}
                cmd = [
                    cmd_info['cmd'],
                    cmd_info['args'], str(count),
                    cmd_info['timeout_arg'], str(timeout * 1000),
                    ip_address
                ]
            else:
                # Linux/macOS: ping -c {count} -W {timeout*1000} {ip}
                cmd = [
                    cmd_info['cmd'],
                    cmd_info['args'], str(count),
                    cmd_info['timeout_arg'], str(timeout * 1000),
                    ip_address
                ]
            
            # Execute ping command
            start_time = time.time()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
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
        """Validate IPv4 address format."""
        pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(pattern, ip):
            parts = ip.split('.')
            return all(0 <= int(part) <= 255 for part in parts)
        return False
    
    def _parse_ping_output(self, output: str, returncode: int, elapsed: float) -> Dict:
        """Parse ping command output and extract latency/status."""
        
        result = {
            'timestamp': datetime.now().isoformat(),
            'packet_loss': 0
        }
        
        # Check if device is reachable (return code 0 means success)
        if returncode == 0:
            result['status'] = 'online'
            
            # Extract latency from output
            latency = self._extract_latency(output)
            result['latency_ms'] = latency
            result['packet_loss'] = 0
            
        else:
            # Device is offline or unreachable
            result['status'] = 'offline'
            result['latency_ms'] = None
            result['packet_loss'] = 100
        
        return result
    
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
        self.monitoring_thread = threading.Thread(
            target=self._monitor_loop,
            args=(device_manager, interval),
            daemon=True
        )
        self.monitoring_thread.start()
        logger.info(f"Started device monitoring (interval: {interval}s)")
    
    def stop_monitoring(self):
        """Stop the monitoring thread."""
        self.is_running = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        logger.info("Stopped device monitoring")
    
    def _monitor_loop(self, device_manager, interval: int):
        """
        Continuous monitoring loop - pings all devices periodically.
        
        Args:
            device_manager: DeviceManager instance
            interval: Interval between ping cycles in seconds
        """
        while self.is_running:
            try:
                # Get all devices to monitor
                # FIX: device_manager is a plain sqlite3-backed DeviceManager —
                # it has no `.session` / ORM query API. Use its real method.
                devices = device_manager.list_devices()
                
                for device in devices:
                    if not self.is_running:
                        break
                    
                    # Ping the device
                    result = self.ping_device(
                        device.ip_address,
                        timeout=device.timeout,
                        count=1
                    )
                    
                    # Record the result
                    device_manager.record_ping_result(
                        device_id=device.id,
                        status=result['status'],
                        latency_ms=result['latency_ms'],
                        packet_loss=result['packet_loss']
                    )
                    
                    # Check for status changes and create alerts
                    old_status = device.status
                    new_status = result['status']
                    
                    if old_status != new_status:
                        # Create status change record
                        device_manager.record_status_change(
                            device.id,
                            old_status,
                            new_status
                        )
                        
                        # Create alert for status change
                        severity = 'critical' if new_status == 'offline' else 'info'
                        device_manager.create_alert(
                            device_id=device.id,
                            alert_type='status_change',
                            severity=severity,
                            message=f'Device {device.name} changed from {old_status} to {new_status}'
                        )
                    
                    # Small delay between device pings
                    time.sleep(0.1)
                
                # Wait for next interval
                time.sleep(interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}")
                time.sleep(interval)
    
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
