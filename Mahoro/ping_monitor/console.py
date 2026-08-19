"""A real command console, for the desktop application only.

The curated checks in ``diagnostics`` cover the commands engineers run constantly.
This module covers the rest: the one-off command you would otherwise open a
terminal to type. It runs through the system shell, so pipes, redirection and
built-ins behave exactly as they do in cmd or bash.

That makes it powerful, which is why it is deliberately fenced:

* **Desktop only.** ``is_enabled()`` returns False unless the process was started
  by ``desktop.py``. Running the same code as a network-facing server leaves the
  console unreachable — an arbitrary-command endpoint on a web server is remote
  code execution, whatever authentication sits in front of it.
* **The user's own privileges.** Nothing is elevated. Everything here is something
  the person sitting at the machine could already type into a terminal; the point
  is to save the typing, not to grant new powers.
* **Everything is recorded.** Command, user, exit code and duration go to the audit
  log, so a shared engineering tool can answer "who ran what".
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

# Enabled by desktop.py at startup. A plain `python app.py` never sets it.
_enabled = False

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
# A runaway command should not be able to exhaust memory or wedge the interface.
MAX_OUTPUT_CHARS = 200_000


def enable() -> None:
    """Called by the desktop launcher. Never called by the web server."""
    global _enabled
    _enabled = True


def is_enabled() -> bool:
    return _enabled


@dataclass
class ConsoleResult:
    command: str
    cwd: str
    exit_code: int
    output: str
    truncated: bool
    duration_ms: int
    timed_out: bool = False

    def as_dict(self) -> dict:
        return {
            'command': self.command,
            'cwd': self.cwd,
            'exit_code': self.exit_code,
            'output': self.output,
            'truncated': self.truncated,
            'duration_ms': self.duration_ms,
            'timed_out': self.timed_out,
            'ok': self.exit_code == 0 and not self.timed_out,
        }


def default_directory() -> str:
    return os.path.expanduser('~')


def run(command: str, cwd: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT) -> ConsoleResult:
    """Run one command line through the shell and capture what it printed."""
    if not is_enabled():
        raise PermissionError(
            'The console is available in the desktop application only.'
        )

    command = (command or '').strip()
    if not command:
        raise ValueError('Type a command to run')

    timeout = max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT))

    working_directory = cwd or default_directory()
    if not os.path.isdir(working_directory):
        raise ValueError(f"'{working_directory}' is not a directory")

    started = time.time()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=working_directory,
            capture_output=True,
            text=True,
            errors='replace',
            timeout=timeout,
        )
        output = (completed.stdout or '') + (completed.stderr or '')
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as e:
        partial = ''
        for stream in (e.stdout, e.stderr):
            if stream:
                partial += stream if isinstance(stream, str) else stream.decode(errors='replace')
        output = partial + f'\n[stopped after {timeout}s]'
        exit_code, timed_out = 124, True
    except OSError as e:
        output, exit_code = f'Could not run the command: {e}', 1

    truncated = len(output) > MAX_OUTPUT_CHARS
    if truncated:
        output = output[:MAX_OUTPUT_CHARS] + '\n[output truncated]'

    return ConsoleResult(
        command=command,
        cwd=working_directory,
        exit_code=exit_code,
        output=output,
        truncated=truncated,
        duration_ms=int((time.time() - started) * 1000),
        timed_out=timed_out,
    )


# Commands an engineer reaches for constantly, offered as one click so the console
# is still faster than a terminal for the common cases.
SNIPPETS = [
    {'group': 'Interfaces', 'items': [
        {'label': 'IP configuration', 'windows': 'ipconfig /all', 'posix': 'ip addr'},
        {'label': 'Routing table', 'windows': 'route print -4', 'posix': 'ip route'},
        {'label': 'Listening ports', 'windows': 'netstat -ano | findstr LISTENING',
         'posix': 'ss -tulpn'},
        {'label': 'ARP table', 'windows': 'arp -a', 'posix': 'ip neigh'},
    ]},
    {'group': 'Reachability', 'items': [
        {'label': 'Ping gateway', 'windows': 'ping -n 4 {gateway}', 'posix': 'ping -c 4 {gateway}'},
        {'label': 'Trace to 8.8.8.8', 'windows': 'tracert -d 8.8.8.8', 'posix': 'traceroute -n 8.8.8.8'},
        {'label': 'Flush DNS cache', 'windows': 'ipconfig /flushdns',
         'posix': 'sudo systemd-resolve --flush-caches'},
        {'label': 'Resolve a name', 'windows': 'nslookup example.com', 'posix': 'dig example.com'},
    ]},
    {'group': 'Wireless', 'items': [
        {'label': 'Wi-Fi interfaces', 'windows': 'netsh wlan show interfaces',
         'posix': 'iwconfig'},
        {'label': 'Saved profiles', 'windows': 'netsh wlan show profiles',
         'posix': 'nmcli connection show'},
    ]},
]


def snippets(gateway: Optional[str] = None) -> list[dict]:
    """The palette, resolved for this platform with the gateway filled in."""
    key = 'windows' if os.name == 'nt' else 'posix'
    resolved = []
    for section in SNIPPETS:
        items = []
        for item in section['items']:
            command = item[key].replace('{gateway}', gateway or '192.168.1.1')
            items.append({'label': item['label'], 'command': command})
        resolved.append({'group': section['group'], 'items': items})
    return resolved
