"""Where files live, in a checkout and inside a packaged executable.

A frozen application has two different roots and conflating them is the usual way
a packaged build fails:

* **Bundled resources** — templates, stylesheets, fonts — are extracted next to the
  executable (or into a temporary directory) and are read-only.
* **User data** — the database, the secret key — must go somewhere writable that
  survives upgrades. Writing it beside the executable breaks the moment the app is
  installed under Program Files, and a one-file build discards it on exit.

Running from a checkout, both resolve back to the repository so development is
unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = 'NetworkMonitor'


def is_frozen() -> bool:
    return getattr(sys, 'frozen', False)


def bundle_root() -> Path:
    """The directory read-only resources were unpacked into."""
    if is_frozen():
        # _MEIPASS is set for one-file builds; one-folder builds sit beside the exe.
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resource(*parts: str) -> str:
    """An absolute path to a bundled, read-only resource."""
    return str(bundle_root().joinpath(*parts))


def data_dir() -> Path:
    """A writable directory for the database and generated secrets.

    Overridable with NETMON_DATA_DIR, which is also how a portable install or a
    test harness points the application somewhere specific.
    """
    override = os.environ.get('NETMON_DATA_DIR')
    if override:
        path = Path(override)
    elif is_frozen():
        if sys.platform == 'win32':
            base = os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local'
        elif sys.platform == 'darwin':
            base = Path.home() / 'Library' / 'Application Support'
        else:
            base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
        path = Path(base) / APP_NAME
    else:
        path = Path(__file__).resolve().parent / 'data'

    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file(name: str) -> str:
    return str(data_dir() / name)
