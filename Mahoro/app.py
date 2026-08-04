"""Flask web app for ping monitoring."""
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_cors import CORS
from functools import wraps
from werkzeug.security import check_password_hash
from ping_monitor.device_manager import DeviceManager
from ping_monitor.ping_service import ping_service
import logging
import os
from datetime import datetime, timedelta

app = Flask(__name__, template_folder='templates', static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize device manager
dm = DeviceManager()


def is_logged_in() -> bool:
    return bool(session.get('user_id'))


def current_user() -> dict:
    if not session.get('user_id'):
        return {}
    return dm.get_user_by_id(session['user_id']) or {}


@app.before_request
def require_login():
    public_endpoints = {
        'login',
        'register',
        'logout',
        'auth_status',
        'static'
    }
    if request.endpoint in public_endpoints:
        return
    if request.path == '/':
        return
    if request.path.startswith('/static/'):
        return
    if not is_logged_in():
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('login'))


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        return fn(*args, **kwargs)
    return wrapper


def is_admin_user() -> bool:
    return current_user().get('role') == 'admin'


@app.route('/')
def index():
    """Serve the admin dashboard."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))
    if user.get('role') != 'admin':
        return redirect(url_for('home'))

    return render_template(
        'index.html',
        username=user.get('display_name') or user.get('username'),
        role=user.get('role'),
        access_denied=False,
    )


@app.route('/home')
def home():
    """Serve a role-based landing page for authenticated users."""
    user = current_user()
    if not user:
        return redirect(url_for('login'))

    return render_template(
        'index.html',
        username=user.get('display_name') or user.get('username'),
        role=user.get('role'),
        access_denied=False,
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    message = request.args.get('message', '')
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        auth_code = request.form.get('auth_code', '').strip()
        user = dm.get_user_by_username(username)

        if user is None or not check_password_hash(user['password_hash'], password):
            message = 'Invalid username or password.'
        elif user.get('mfa_enabled') and auth_code:
            if dm.verify_mfa_code(user, auth_code):
                pass
            elif dm.verify_backup_code(user, auth_code):
                dm.clear_backup_code(user['id'])
            else:
                message = 'Invalid MFA or backup code. Please enter the 6-digit code from your authenticator app or the backup code from registration.'

        if not message:
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            dm.update_user_last_login(user['id'])
            if user['role'] == 'admin':
                return redirect(url_for('index'))
            return redirect(url_for('home'))

    return render_template('login.html', message=message, user_count=dm.get_user_count())


@app.route('/register', methods=['GET', 'POST'])
def register():
    message = ''
    success = False
    secret = None
    first_user = dm.get_user_count() == 0

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        phone_number = request.form.get('phone_number', '').strip() or None
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm', '').strip()

        if password != confirm:
            message = 'Passwords do not match.'
        else:
            try:
                user = dm.create_user(
                    username=username,
                    password=password,
                    display_name=display_name,
                    phone_number=phone_number,
                    role='operator',
                    mfa_enabled=True,
                )
                secret = user['mfa_secret']
                backup_code = user['backup_code']
                success = True
                message = 'Registration successful. Store your MFA secret and backup code securely.'
            except Exception as e:
                message = str(e)

    return render_template(
        'register.html',
        message=message,
        success=success,
        secret=secret if 'secret' in locals() else None,
        backup_code=backup_code if 'backup_code' in locals() else None,
        first_user=first_user
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    if not is_logged_in():
        return jsonify({'authenticated': False}), 200
    user = current_user()
    return jsonify({
        'authenticated': True,
        'username': user.get('username'),
        'display_name': user.get('display_name'),
        'role': user.get('role'),
        'is_admin': user.get('role') == 'admin'
    })


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
@admin_required
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
        if data.get('group_name'):
            dm.assign_device_to_group(device.id, data['group_name'])
            device = dm.get_device(device.id)

        return jsonify({
            'id': device.id,
            'name': device.name,
            'ip_address': device.ip_address,
            'group_name': device.group_name,
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
@admin_required
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
@admin_required
def delete_device(device_id):
    """Delete a device."""
    try:
        dm.delete_device(device_id)
        return jsonify({'message': 'Device deleted'}), 200
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/api/devices/<int:device_id>/assign-group', methods=['POST'])
@admin_required
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


# ==================== USERS ====================
@app.route('/api/users', methods=['GET'])
@admin_required
def get_users():
    """Get all user accounts and roles."""
    try:
        return jsonify(dm.list_users())
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return jsonify({'error': str(e)}), 500


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
@admin_required
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
    """Perform a manual ping on a device and optionally record the result."""
    try:
        device = dm.get_device_by_ip(ip_address)
        result = ping_service.ping_device(ip_address)

        if device:
            updated_device = dm.record_ping_result(
                device_id=device.id,
                status=result['status'],
                latency_ms=result['latency_ms'],
                packet_loss=result.get('packet_loss', 0)
            )

            if device.status != updated_device.status:
                severity = 'critical' if updated_device.status == 'offline' else 'info'
                dm.create_alert(
                    device_id=device.id,
                    alert_type='manual_ping',
                    severity=severity,
                    message=f'Manual ping changed {device.name} from {device.status} to {updated_device.status}'
                )

            result.update({
                'device_id': device.id,
                'device_name': device.name,
                'status': updated_device.status,
                'last_latency_ms': updated_device.last_latency_ms,
            })

        return jsonify(result), 200
    except Exception as e:
        logger.error(f"Error pinging {ip_address}: {e}")
        return jsonify({'error': str(e), 'status': 'error'}), 500


@app.route('/api/monitoring/start', methods=['POST'])
@admin_required
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
@admin_required
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


@app.route('/api/settings/network-gateway', methods=['GET', 'POST'])
@admin_required
def network_gateway_settings():
    """Store and return the network gateway IP for the current environment."""
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        gateway_ip = (payload.get('gateway_ip') or '').strip()
        if not gateway_ip:
            return jsonify({'error': 'Gateway IP is required'}), 400
        session['network_gateway'] = gateway_ip
        return jsonify({'gateway_ip': gateway_ip, 'saved': True}), 200

    gateway_ip = session.get('network_gateway') or ''
    return jsonify({'gateway_ip': gateway_ip}), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
