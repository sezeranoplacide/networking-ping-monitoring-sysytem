"""Desktop launcher for Network Monitor.

Runs the application in a native window instead of a browser. This is also what
makes the command console safe to offer: the server is bound to loopback on a
random port and is never advertised, so the only thing that can drive it is the
window on this machine. The same code started as `python app.py` leaves the
console switched off.

    python desktop.py

Packaging into a single executable:

    pip install pyinstaller
    pyinstaller --noconfirm networkmonitor.spec
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from urllib.request import urlopen

logger = logging.getLogger('desktop')

WINDOW_TITLE = 'Network Monitor'
MIN_SIZE = (1100, 720)


def free_port() -> int:
    """Ask the OS for an unused port, bound to loopback only."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_until_serving(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{port}/login', timeout=1):
                return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Bind before importing the app so the child threads it starts inherit nothing
    # unexpected, and so a port collision fails early and clearly.
    port = free_port()

    os.environ.setdefault('HOST', '127.0.0.1')
    os.environ['PORT'] = str(port)
    os.environ['FLASK_DEBUG'] = '0'

    import app as application
    from ping_monitor import console
    from ping_monitor.ping_service import ping_service

    # The console is a desktop capability. Nothing else in the codebase turns it on.
    console.enable()
    logger.info('Console enabled for this desktop session')

    # First run seeds an administrator with a generated password. Say where to find
    # it, rather than leaving the owner locked out of their own installation.
    first_run = application.dm.first_run_password_file()
    if first_run.exists():
        logger.warning('First run — sign-in details are in %s', first_run)

    try:
        import webview
    except ImportError:
        print('pywebview is not installed. Run:  pip install pywebview', file=sys.stderr)
        return 1

    def serve() -> None:
        try:
            application.app.run(host='127.0.0.1', port=port, debug=False,
                                use_reloader=False, threaded=True)
        except Exception as e:
            logger.error('Server stopped: %s', e)

    threading.Thread(target=serve, daemon=True, name='http').start()

    if application.dm.list_devices():
        ping_service.start_monitoring(application.dm, interval=5)
    application.start_notification_escalator()

    if not wait_until_serving(port):
        print('The application did not start in time.', file=sys.stderr)
        return 1

    logger.info('Serving privately on 127.0.0.1:%s', port)

    webview.create_window(
        WINDOW_TITLE,
        f'http://127.0.0.1:{port}/',
        width=1380, height=880,
        min_size=MIN_SIZE,
        text_select=True,
    )
    # Blocks until the window is closed; the daemon threads go with it.
    webview.start()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
