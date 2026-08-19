"""Flask web app for ping monitoring."""
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_cors import CORS
from functools import wraps
from werkzeug.security import check_password_hash
from ping_monitor.device_manager import DEVICE_TYPES, DeviceManager, normalize_target
from ping_monitor.ping_service import ping_service
from ping_monitor import console, diagnostics, paths
import hmac
import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _load_secret_key() -> str:
    """Use SECRET_KEY from the environment, or a persisted random key.

    The previous fallback was the literal string 'change-this-secret', which is
    published in this repository and makes every session cookie forgeable.
    """
    from_env = os.environ.get('SECRET_KEY')
    if from_env:
        return from_env

    key_file = Path(paths.data_file('.secret_key'))
    if key_file.exists():
        return key_file.read_text(encoding='utf-8').strip()

    key = secrets.token_urlsafe(48)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key, encoding='utf-8')
    logger.warning(
        'SECRET_KEY was not set. Generated one at %s — set SECRET_KEY in the '
        'environment before deploying.', key_file
    )
    return key


# Absolute paths: a packaged build relocates the module, so Flask's own relative
# resolution would look for templates in the wrong place.
app = Flask(
    __name__,
    template_folder=paths.resource('templates'),
    static_folder=paths.resource('static'),
)
app.secret_key = _load_secret_key()
app.config.update(
    SEND_FILE_MAX_AGE_DEFAULT=0,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Set COOKIE_SECURE=1 once the app is served over HTTPS.
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '0') == '1',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

# Same-origin by default. CORS_ORIGINS opens specific origins when a separate
# frontend needs them; the previous CORS(app) reflected any origin that asked.
_cors_origins = [o.strip() for o in os.environ.get('CORS_ORIGINS', '').split(',') if o.strip()]
if _cors_origins:
    CORS(app, origins=_cors_origins, supports_credentials=True)

# Initialize device manager
dm = DeviceManager()

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}

# Login throttling. In-memory, so it resets on restart and is per-process — enough
# to stop online guessing, not a substitute for a real rate limiter in production.
LOGIN_MAX_ATTEMPTS = 8
LOGIN_LOCKOUT_SECONDS = 300
_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_lock = threading.Lock()


def _throttle_key() -> str:
    return request.remote_addr or 'unknown'


def login_is_locked() -> bool:
    now = time.time()
    with _login_lock:
        attempts = [t for t in _login_attempts[_throttle_key()] if now - t < LOGIN_LOCKOUT_SECONDS]
        _login_attempts[_throttle_key()] = attempts
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_failed_login() -> None:
    with _login_lock:
        _login_attempts[_throttle_key()].append(time.time())


def clear_failed_logins() -> None:
    with _login_lock:
        _login_attempts.pop(_throttle_key(), None)


def csrf_token() -> str:
    token = session.get('csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['csrf_token'] = token
    return token


def asset(filename: str) -> str:
    """Static URL stamped with the file's modification time.

    Without this a browser keeps serving the JavaScript and CSS it cached before
    an update, and the user tests an old build without knowing it.
    """
    url = url_for('static', filename=filename)
    try:
        stamp = int((Path(app.static_folder) / filename).stat().st_mtime)
        return f'{url}?v={stamp}'
    except OSError:
        return url


@app.context_processor
def inject_template_helpers():
    return {'csrf_token': csrf_token, 'asset': asset}


@app.after_request
def set_security_headers(response):
    # Pages are rendered per request and reflect live state, so a cached copy is
    # always wrong. Without this a browser can keep serving an older page — and the
    # person is then looking at an interface that no longer matches the server.
    if response.mimetype == 'text/html':
        response.headers.setdefault('Cache-Control', 'no-store, must-revalidate')

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'"
    )
    if app.config.get('SESSION_COOKIE_SECURE'):
        response.headers.setdefault(
            'Strict-Transport-Security', 'max-age=31536000; includeSubDomains'
        )
    return response


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

    if request.method not in SAFE_METHODS and request.endpoint != 'static':
        submitted = (
            request.headers.get('X-CSRF-Token')
            or request.form.get('csrf_token')
            or (request.get_json(silent=True) or {}).get('csrf_token')
        )
        expected = session.get('csrf_token')
        # Login and registration establish the session, so they carry a token
        # only once the visitor has loaded the form — which they always have.
        if not expected or not submitted or not hmac.compare_digest(str(submitted), str(expected)):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Invalid or missing CSRF token'}), 403
            return render_template(
                'login.html',
                message='Your session expired. Please try again.',
                user_count=dm.get_user_count(),
                first_run_hint=None,
            ), 403

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

    # A role or an account can be revoked mid-session; check the database, not the
    # cookie, which was previously trusted for the full 8-hour session lifetime.
    user = current_user()
    if not user or not user.get('is_active', 1):
        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Account is no longer active'}), 401
        return redirect(url_for('login', message='Your account is no longer active.'))
    session['role'] = user.get('role')


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Read the role from the database, not the session it was stamped into at
        # login — otherwise revoking admin takes effect only at the next login.
        if not is_admin_user():
            return jsonify({'error': 'Admin access required'}), 403
        return fn(*args, **kwargs)
    return wrapper


def is_admin_user() -> bool:
    return current_user().get('role') == 'admin'


def can_run_diagnostics() -> bool:
    """Who may make this server probe the network on their behalf."""
    return current_user().get('role') in ('admin', 'network_engineer')


def diagnostics_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not can_run_diagnostics():
            return jsonify({'error': 'Diagnostics require an engineer or admin account'}), 403
        return fn(*args, **kwargs)
    return wrapper


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

        if login_is_locked():
            message = 'Too many failed attempts. Try again in a few minutes.'
            return render_template(
                'login.html', message=message, user_count=dm.get_user_count()
            ), 429

        user = dm.get_user_by_username(username)

        if user is None or not check_password_hash(user['password_hash'], password):
            message = 'Invalid username or password.'
        elif not user.get('is_active', 1):
            message = 'This account is awaiting administrator approval.'
        elif user.get('mfa_enabled'):
            # A blank code used to skip this branch entirely and log the user
            # straight in. If MFA is enabled, a valid code is required.
            if not auth_code:
                message = 'Enter the 6-digit code from your authenticator app, or a backup code.'
            elif dm.verify_mfa_code(user, auth_code):
                pass
            elif dm.verify_backup_code(user, auth_code):
                dm.clear_backup_code(user['id'])
            else:
                message = 'Invalid MFA or backup code. Please enter the 6-digit code from your authenticator app or the backup code from registration.'

        if not message:
            clear_failed_logins()
            # New session identifier on privilege change, so a pre-login cookie
            # cannot be reused after authentication.
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            csrf_token()
            dm.update_user_last_login(user['id'])
            if user['role'] == 'admin':
                return redirect(url_for('index'))
            return redirect(url_for('home'))

        record_failed_login()

    # Only while the seeded password is still in force — the file is removed the
    # moment it is changed, and with it this hint.
    hint = dm.first_run_password_file()
    return render_template(
        'login.html', message=message, user_count=dm.get_user_count(),
        first_run_hint=str(hint) if hint.exists() else None,
    )


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
                    # Self-registered accounts wait for an administrator. Anyone
                    # could previously register and immediately read the full
                    # device inventory.
                    is_active=first_user,
                )
                secret = user['mfa_secret']
                backup_code = user['backup_code']
                success = True
                message = (
                    'Registration successful. Store your MFA secret and backup code securely.'
                    if user['is_active'] else
                    'Registration received. An administrator must approve your account '
                    'before you can sign in. Store your MFA secret and backup code now — '
                    'they are shown only once.'
                )
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
        return jsonify({'authenticated': False, 'csrf_token': csrf_token()}), 200
    user = current_user()
    return jsonify({
        'authenticated': True,
        'username': user.get('username'),
        'display_name': user.get('display_name'),
        'role': user.get('role'),
        'is_admin': user.get('role') == 'admin',
        'csrf_token': csrf_token(),
    })


# ==================== DEVICES ====================
@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get all devices."""
    try:
        devices = dm.get_devices()
        # These are real columns now. They were read with getattr defaults against
        # a dataclass that never had them, so uptime was always reported as 100%.
        return jsonify([
            {
                'id': d.id,
                'name': d.name,
                'ip_address': d.ip_address,
                'group_name': d.group_name,
                'interval': d.interval,
                'timeout': d.timeout,
                'status': d.status,
                'last_latency_ms': d.last_latency_ms,
                'min_latency_ms': d.min_latency_ms,
                'max_latency_ms': d.max_latency_ms,
                'avg_latency_ms': d.avg_latency_ms,
                'uptime_percentage': d.uptime_percentage,
                'device_type': d.device_type,
                'parent_id': d.parent_id,
                'total_requests': d.total_requests,
                'successful_requests': d.successful_requests,
                'failed_requests': d.failed_requests,
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
        parent_id = data.get('parent_id')
        device = dm.create_device(
            name=data['name'],
            ip_address=data['ip_address'],
            interval=data.get('interval', 5),
            timeout=data.get('timeout', 2),
            device_type=data.get('device_type', 'other'),
            parent_id=int(parent_id) if parent_id not in (None, '', 'null') else None,
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
            'status': device.status,
            'device_type': device.device_type,
            'parent_id': device.parent_id,
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
        data = request.json or {}
        kwargs = {
            'name': data.get('name'),
            'ip_address': data.get('ip_address'),
            'interval': data.get('interval'),
            'timeout': data.get('timeout'),
            'device_type': data.get('device_type'),
        }
        # Only forward the topology fields the client actually sent, so a partial
        # update never silently unplaces a device or clears its group.
        if 'parent_id' in data:
            raw = data['parent_id']
            kwargs['parent_id'] = int(raw) if raw not in (None, '', 'null') else None
        if 'group_name' in data:
            kwargs['group_name'] = data['group_name'] or None

        device = dm.update_device(device_id=device_id, **kwargs)
        return jsonify({
            'id': device.id,
            'name': device.name,
            'ip_address': device.ip_address,
            'interval': device.interval,
            'timeout': device.timeout,
            'status': device.status,
            'device_type': device.device_type,
            'parent_id': device.parent_id,
            'group_name': device.group_name,
        })
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
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


# ==================== CONSOLE ====================
@app.route('/api/console', methods=['GET'])
def console_status():
    """Whether the console is available here, and the palette for this platform."""
    return jsonify({
        'enabled': console.is_enabled(),
        'can_run': can_run_diagnostics(),
        'cwd': console.default_directory(),
        'snippets': console.snippets(dm.get_setting('network_gateway')),
        'reason': None if console.is_enabled() else (
            'The console runs only in the desktop application. Reached over a '
            'network, an open command endpoint would be remote code execution.'
        ),
    }), 200


@app.route('/api/console/run', methods=['POST'])
@diagnostics_required
def console_run():
    """Run one command line on this machine.

    Desktop application only — see the fencing described in ping_monitor/console.py.
    """
    if not console.is_enabled():
        return jsonify({
            'error': 'The console is available in the desktop application only.'
        }), 403

    data = request.json or {}
    try:
        result = console.run(
            data.get('command', ''),
            cwd=data.get('cwd') or None,
            timeout=data.get('timeout') or console.DEFAULT_TIMEOUT,
        )
    except PermissionError as e:
        return jsonify({'error': str(e)}), 403
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    dm.record_command(
        username=session.get('username'),
        command=result.command,
        cwd=result.cwd,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
    )
    return jsonify(result.as_dict()), 200


@app.route('/api/console/history', methods=['GET'])
@diagnostics_required
def console_history():
    """What has been run on this machine, by whom."""
    limit = request.args.get('limit', 100, type=int)
    return jsonify(dm.get_command_log(limit=limit)), 200


# ==================== DIAGNOSTICS ====================
@app.route('/api/diagnostics', methods=['GET'])
def diagnostics_catalogue():
    """The checks available, and the things worth pointing them at."""
    devices = dm.get_devices()
    return jsonify({
        'operations': diagnostics.catalogue(),
        'can_run': can_run_diagnostics(),
        'gateway': dm.get_setting('network_gateway') or '',
        'targets': [
            {'id': d.id, 'name': d.name, 'address': d.ip_address,
             'device_type': d.device_type, 'status': d.status}
            for d in devices
        ],
    }), 200


@app.route('/api/diagnostics/run', methods=['POST'])
@diagnostics_required
def diagnostics_run():
    """Run one named check. Callers choose from the catalogue; they never
    supply a command line."""
    data = request.json or {}
    try:
        result = diagnostics.run(
            data.get('operation', ''),
            target=(data.get('target') or '').strip() or None,
            port=data.get('port'),
        )
        return jsonify(result.as_dict()), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Diagnostics failed: {e}")
        return jsonify({'error': 'The check could not be completed'}), 500


@app.route('/api/diagnostics/locate/<int:device_id>', methods=['POST'])
@diagnostics_required
def diagnostics_locate(device_id):
    """Work out where a device's connectivity actually breaks.

    Runs the sequence an engineer runs by hand — check the gateway, walk the path,
    check the local segment — and reports the closest point to the fault instead of
    leaving them to read three command outputs and infer it.
    """
    device = dm.get_device(device_id)
    if device is None:
        return jsonify({'error': 'Device not found'}), 404

    steps = []
    gateway = dm.get_setting('network_gateway')

    if gateway:
        steps.append(diagnostics.run('ping', target=gateway))
    steps.append(diagnostics.run('ping', target=device.ip_address))

    reachable = steps[-1].ok
    if not reachable:
        steps.append(diagnostics.run('trace', target=device.ip_address))
        steps.append(diagnostics.run('arp', target=device.ip_address))

    # The topology already knows whether something upstream is down; the trace says
    # where the path dies. Together they name one place to go and look.
    culprit = dm.find_fault_domain(device_id)
    trace = next((s for s in steps if s.operation == 'trace'), None)
    gateway_step = steps[0] if gateway else None

    if reachable:
        verdict = f'{device.name} is answering. No fault to locate.'
    elif culprit is not None:
        verdict = (f'{device.name} sits behind {culprit.name} ({culprit.ip_address}), '
                   f'which is also down. Fix {culprit.name} first.')
    elif gateway_step is not None and not gateway_step.ok:
        verdict = (f'The gateway {gateway} is not answering either — this looks like '
                   f'a problem at or before the gateway, not at {device.name}.')
    elif trace is not None and trace.detail.get('last_responding_hop'):
        hop = trace.detail['last_responding_hop']
        verdict = (f"The path to {device.name} stops after {hop['address']} "
                   f"(hop {hop['hop']}). That device or its link is the place to start.")
    else:
        verdict = (f'{device.name} is not answering and the path could not be traced. '
                   f'Check the local segment and cabling.')

    return jsonify({
        'device': {'id': device.id, 'name': device.name, 'address': device.ip_address},
        'reachable': reachable,
        'verdict': verdict,
        'blocked_by': ({'id': culprit.id, 'name': culprit.name,
                        'address': culprit.ip_address} if culprit else None),
        'steps': [s.as_dict() for s in steps],
    }), 200


@app.route('/api/diagnostics/detect-gateway', methods=['POST'])
@diagnostics_required
def diagnostics_detect_gateway():
    """Read the default gateway off this host's routing table."""
    result = diagnostics.run('routes')
    detected = result.detail.get('default_gateway')
    if not detected:
        return jsonify({'error': 'No default route found on this host'}), 404
    return jsonify({'gateway_ip': detected, 'command': result.command}), 200


# ==================== TOPOLOGY ====================
@app.route('/api/topology', methods=['GET'])
def get_topology():
    """The network as a connected tree, with root-cause attribution."""
    try:
        return jsonify(dm.get_topology()), 200
    except Exception as e:
        logger.error(f"Error building topology: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/device-types', methods=['GET'])
def get_device_types():
    """Device types and which of them other devices can connect to."""
    return jsonify([
        {'value': key, 'label': meta['label'], 'infrastructure': meta['infrastructure']}
        for key, meta in DEVICE_TYPES.items()
    ]), 200


@app.route('/api/devices/<int:device_id>/uplink', methods=['PUT'])
@admin_required
def set_device_uplink(device_id):
    """Connect a device to the switch or router it hangs off."""
    try:
        data = request.json or {}
        parent_id = data.get('parent_id')
        device = dm.set_device_parent(
            device_id,
            int(parent_id) if parent_id not in (None, '', 'null') else None,
        )
        return jsonify({'id': device.id, 'parent_id': device.parent_id}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error setting uplink: {e}")
        return jsonify({'error': str(e)}), 400


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


@app.route('/api/users/<int:user_id>/role', methods=['PUT'])
@admin_required
def update_user_role(user_id):
    try:
        data = request.json or {}
        role = data.get('role')
        if not role:
            return jsonify({'error': 'Role is required'}), 400
        user = dm.update_user_role(user_id, role)
        return jsonify(user), 200
    except Exception as e:
        logger.error(f"Error updating user role: {e}")
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


@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    try:
        # Return recent notifications (admins see all, others see same feed for now)
        notes = dm.get_notifications()
        return jsonify(notes), 200
    except Exception as e:
        logger.error(f"Error fetching notifications: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/notifications/<int:notification_id>/ack', methods=['POST'])
def ack_notification(notification_id):
    try:
        dm.acknowledge_notification(notification_id)
        return jsonify({'success': True}), 200
    except Exception as e:
        logger.error(f"Error acknowledging notification: {e}")
        return jsonify({'error': str(e)}), 400


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
@app.route('/api/ping/<ip_address>', methods=['POST'])
def manual_ping(ip_address):
    # POST, not GET: this writes a ping result and can raise an alert. As a GET it
    # was a state-changing read that any cross-site navigation could trigger.
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


def start_notification_escalator(interval_seconds: int = 60, older_than_minutes: int = 5):
    """Escalate critical notifications nobody has acknowledged."""

    def worker():
        logger.info('Notification escalator started')
        while True:
            try:
                count = dm.escalate_unacknowledged_notifications(older_than_minutes)
                if count:
                    logger.info(f'Escalated {count} notifications')
            except Exception as e:
                logger.error(f'Error in escalator: {e}')
            time.sleep(interval_seconds)

    t = threading.Thread(target=worker, daemon=True, name='escalator')
    t.start()


@app.route('/api/settings/network-gateway', methods=['GET', 'POST'])
@admin_required
def network_gateway_settings():
    """Store and return the network gateway IP for this installation."""
    if request.method == 'POST':
        payload = request.get_json(silent=True) or {}
        gateway_ip = (payload.get('gateway_ip') or '').strip()
        if not gateway_ip:
            return jsonify({'error': 'Gateway IP is required'}), 400
        try:
            gateway_ip = normalize_target(gateway_ip)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        # Stored in the database, not the session: one gateway for the
        # installation, shared by every administrator and surviving logout.
        dm.set_setting('network_gateway', gateway_ip)
        return jsonify({'gateway_ip': gateway_ip, 'saved': True}), 200

    return jsonify({'gateway_ip': dm.get_setting('network_gateway', '') or ''}), 200


@app.route('/api/account/password', methods=['PUT'])
def change_own_password():
    """Change the signed-in user's password."""
    try:
        data = request.json or {}
        dm.change_password(
            session['user_id'],
            current_password=data.get('current_password', ''),
            new_password=data.get('new_password', ''),
        )
        return jsonify({'success': True, 'message': 'Password updated'}), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error changing password: {e}")
        return jsonify({'error': 'Could not change password'}), 400


@app.route('/api/account/security', methods=['GET'])
def account_security():
    """Warnings the signed-in user needs to act on."""
    warnings = []
    if is_admin_user() and dm.uses_default_password(session.get('username', '')):
        warnings.append({
            'id': 'default_password',
            'severity': 'critical',
            'message': ('This account still uses the default password published in '
                        'the source repository. Change it now.'),
        })
    return jsonify({'warnings': warnings}), 200


@app.route('/api/users/<int:user_id>/active', methods=['PUT'])
@admin_required
def update_user_active(user_id):
    """Approve or suspend an account."""
    try:
        data = request.json or {}
        if 'is_active' not in data:
            return jsonify({'error': 'is_active is required'}), 400
        user = dm.set_user_active(user_id, bool(data['is_active']))
        return jsonify(user), 200
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating account status: {e}")
        return jsonify({'error': str(e)}), 400


if __name__ == '__main__':
    # Debug and a public bind are opt-in. Debug mode exposes the Werkzeug console,
    # which is remote code execution for anyone who can reach a traceback.
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '5000'))

    if os.environ.get('AUTOSTART_MONITORING', '1') == '1' and not debug:
        ping_service.start_monitoring(dm, interval=5)
    start_notification_escalator()

    logger.info('Serving on http://%s:%s (debug=%s)', host, port, debug)
    app.run(debug=debug, host=host, port=port)
