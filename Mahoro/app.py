"""Flask web app for ping monitoring."""
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from ping_monitor.device_manager import DeviceManager
from ping_monitor.ping_service import ping_service
import logging
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize device manager
dm = DeviceManager()


@app.route('/')
def index():
    """Serve main page."""
    return render_template('index.html')


# ==================== DEVICES ====================
@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get all devices."""
    try:
        devices = dm.get_devices()
        return jsonify([
            {
                'id': d.id,
                'name': d.name,
                'ip_address': d.ip_address,
                'group_name': getattr(d, 'group_name', None),
                'interval': d.interval,
                'timeout': d.timeout,
                'status': d.status,
                'last_latency_ms': d.last_latency_ms,
                'min_latency_ms': getattr(d, 'min_latency_ms', None),
                'max_latency_ms': getattr(d, 'max_latency_ms', None),
                'avg_latency_ms': getattr(d, 'avg_latency_ms', None),
                'uptime_percentage': getattr(d, 'uptime_percentage', 100),
                'last_seen_at': d.last_seen_at,
                'created_at': d.created_at,
                'updated_at': d.updated_at
            }
            for d in devices
        ])
    except Exception as e:
        logger.error(f"Error fetching devices: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/devices', methods=['POST'])
def create_device():
    """Create a new device."""
    try:
        data = request.json
        device = dm.create_device(
            name=data['name'],
            ip_address=data['ip_address'],
            interval=data.get('interval', 5),
            timeout=data.get('timeout', 2)
        )
        return jsonify({
            'id': device.id,
            'name': device.name,
            'ip_address': device.ip_address,
            'interval': device.interval,
            'timeout': device.timeout,
            'status': device.status
        }), 201
    except Exception as e:
        logger.error(f"Error creating device: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/devices/<int:device_id>', methods=['GET'])
def get_device(device_id):
    """Get a specific device."""
    try:
        device = dm.get_device_by_id(device_id)
        if not device:
            return jsonify({'error': 'Device not found'}), 404
        return jsonify({
            'id': device.id,
            'name': device.name,
            'ip_address': device.ip_address,
            'interval': device.interval,
            'timeout': device.timeout,
            'status': device.status,
            'last_latency_ms': device.last_latency_ms,
            'last_seen_at': device.last_seen_at
        })
    except Exception as e:
        logger.error(f"Error fetching device: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/devices/<int:device_id>', methods=['PUT'])
def update_device(device_id):
    """Update a device."""
    try:
        data = request.json
        device = dm.update_device(
            device_id=device_id,
            name=data.get('name'),
            interval=data.get('interval'),
            timeout=data.get('timeout')
        )
        return jsonify({
            'id': device.id,
            'name': device.name,
            'ip_address': device.ip_address,
            'interval': device.interval,
            'timeout': device.timeout,
            'status': device.status
        })
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/devices/<int:device_id>', methods=['DELETE'])
def delete_device(device_id):
    """Delete a device."""
    try:
        dm.delete_device(device_id)
        return jsonify({'message': 'Device deleted'}), 200
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/devices/<int:device_id>/assign-group', methods=['POST'])
def assign_group(device_id):
    """Assign device to a group."""
    try:
        data = request.json
        device = dm.assign_device_to_group(device_id, data['group_name'])
        return jsonify({'success': True, 'group_name': device.group_name})
    except Exception as e:
        logger.error(f"Error assigning group: {e}")
        return jsonify({'error': str(e)}), 400


# ==================== PING HISTORY ====================
@app.route('/api/devices/<int:device_id>/history', methods=['GET'])
def get_device_history(device_id):
    """Get ping history for a device."""
    try:
        limit = request.args.get('limit', 100, type=int)
        history = dm.get_ping_history(device_id, limit=limit)
        return jsonify([
            {
                'timestamp': h.timestamp,
                'latency_ms': h.latency_ms,
                'status': h.status
            }
            for h in history
        ])
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== STATISTICS ====================
@app.route('/api/devices/<int:device_id>/statistics', methods=['GET'])
def get_device_stats(device_id):
    """Get comprehensive device statistics."""
    try:
        hours = request.args.get('hours', 24, type=int)
        stats = dm.get_device_statistics(device_id, hours=hours)
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error fetching statistics: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/network/summary', methods=['GET'])
def get_network_summary():
    """Get network-wide summary."""
    try:
        summary = dm.get_network_summary()
        return jsonify(summary)
    except Exception as e:
        logger.error(f"Error fetching network summary: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== TIMELINE ====================
@app.route('/api/devices/<int:device_id>/timeline', methods=['GET'])
def get_device_timeline(device_id):
    """Get status change timeline."""
    try:
        limit = request.args.get('limit', 50, type=int)
        timeline = dm.get_status_timeline(device_id, limit=limit)
        return jsonify(timeline)
    except Exception as e:
        logger.error(f"Error fetching timeline: {e}")
        return jsonify({'error': str(e)}), 500


# ==================== ALERTS ====================
@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    """Get all alerts or filter by device."""
    try:
        device_id = request.args.get('device_id', type=int)
        unacknowledged = request.args.get('unacknowledged', False, type=lambda x: x.lower() == 'true')
        alerts = dm.get_alerts(device_id=device_id, unacknowledged_only=unacknowledged)
        return jsonify(alerts)
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/alerts/<int:alert_id>/acknowledge', methods=['POST'])
def acknowledge_alert(alert_id):
    """Acknowledge an alert."""
    try:
        dm.acknowledge_alert(alert_id)
        return jsonify({'success': True, 'message': 'Alert acknowledged'})
    except Exception as e:
        logger.error(f"Error acknowledging alert: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/devices/<int:device_id>/alert', methods=['POST'])
def create_alert(device_id):
    """Create an alert."""
    try:
        data = request.json
        alert = dm.create_alert(
            device_id=device_id,
            alert_type=data.get('alert_type', 'status_change'),
            severity=data.get('severity', 'info'),
            message=data['message']
        )
        return jsonify(alert), 201
    except Exception as e:
        logger.error(f"Error creating alert: {e}")
        return jsonify({'error': str(e)}), 400


# ==================== GROUPS ====================
@app.route('/api/groups', methods=['GET'])
def get_groups():
    """Get all device groups."""
    try:
        groups = dm.get_device_groups()
        return jsonify(groups)
    except Exception as e:
        logger.error(f"Error fetching groups: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/groups', methods=['POST'])
def create_group():
    """Create a new device group."""
    try:
        data = request.json
        group = dm.create_device_group(
            name=data['name'],
            description=data.get('description', ''),
            color=data.get('color', '#667eea')
        )
        return jsonify(group), 201
    except Exception as e:
        logger.error(f"Error creating group: {e}")
        return jsonify({'error': str(e)}), 400


# ==================== PING MONITORING ====================
@app.route('/api/ping/<ip_address>', methods=['GET'])
def manual_ping(ip_address):
    """Perform a manual ping on a device."""
    try:
        result = ping_service.ping_device(ip_address)
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error pinging {ip_address}: {e}")
        return jsonify({'error': str(e), 'status': 'error'}), 500


@app.route('/api/monitoring/start', methods=['POST'])
def start_monitoring():
    """Start automatic device monitoring."""
    try:
        interval = request.json.get('interval', 5) if request.json else 5
        if ping_service.is_running:
            return jsonify({'message': 'Monitoring already running'}), 200
        
        ping_service.start_monitoring(dm, interval=interval)
        return jsonify({'success': True, 'message': f'Monitoring started (interval: {interval}s)'}), 200
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/monitoring/stop', methods=['POST'])
def stop_monitoring():
    """Stop automatic device monitoring."""
    try:
        ping_service.stop_monitoring()
        return jsonify({'success': True, 'message': 'Monitoring stopped'}), 200
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/monitoring/status', methods=['GET'])
def monitoring_status():
    """Get current monitoring status."""
    return jsonify({
        'is_running': ping_service.is_running,
        'last_results': len(ping_service.last_results)
    }), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
