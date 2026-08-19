"""Network diagnostics an engineer would otherwise run in a terminal.

The point of this module is to remove the retyping. Every check here is one an
engineer already performs by hand — trace the path, resolve a name, test a port,
read the ARP table — and each is exposed as a named operation with validated
arguments and parsed output.

Two rules hold throughout:

* There is no arbitrary command execution. Callers name an operation from
  ``OPERATIONS``; they never supply a command line. Nothing is run through a
  shell, and every argument is validated before it reaches ``subprocess``.
* Every result carries the exact command that produced it. An engineer has to be
  able to see what was run and reproduce it, otherwise the tool is asking for
  trust it has not earned.
"""
from __future__ import annotations

import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .device_manager import normalize_target

SYSTEM = platform.system()

# A trace can legitimately take a while; everything else should be quick.
DEFAULT_TIMEOUT = 15
TRACE_TIMEOUT = 60
MAX_HOPS = 20


@dataclass
class Result:
    """One diagnostic run, ready to render."""
    operation: str
    label: str
    target: Optional[str]
    command: str
    ok: bool
    summary: str
    output: str = ''
    detail: dict = field(default_factory=dict)
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            'operation': self.operation,
            'label': self.label,
            'target': self.target,
            'command': self.command,
            'ok': self.ok,
            'summary': self.summary,
            'output': self.output,
            'detail': self.detail,
            'duration_ms': self.duration_ms,
        }


def _run(args: list[str], timeout: int) -> tuple[str, int]:
    """Run an allow-listed command. Never a shell, never a caller-supplied string."""
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, errors='replace', timeout=timeout
        )
        return (completed.stdout or '') + (completed.stderr or ''), completed.returncode
    except subprocess.TimeoutExpired:
        return f'Timed out after {timeout}s.', 1
    except FileNotFoundError:
        return f"'{args[0]}' is not available on this host.", 1


# ---------------------------------------------------------------- reachability


def op_ping(target: str, **_) -> Result:
    count = '4'
    if SYSTEM == 'Windows':
        args = ['ping', '-n', count, '-w', '2000', target]
    else:
        args = ['ping', '-c', count, '-W', '2', target]

    started = time.time()
    output, _code = _run(args, DEFAULT_TIMEOUT)
    elapsed = int((time.time() - started) * 1000)

    times = [float(m) for m in re.findall(r'time[=<]\s*([\d.]+)\s*ms', output, re.I)]
    loss = re.search(r'\((\d+)%\s*(?:packet\s*)?loss', output, re.I)
    loss_pct = int(loss.group(1)) if loss else (0 if times else 100)
    unreachable = re.search(r'(destination .*unreachable|ttl expired)', output, re.I)

    ok = bool(times) and loss_pct < 100 and not unreachable
    if unreachable:
        summary = f'No route — {unreachable.group(1).strip().lower()}'
    elif not times:
        summary = 'No reply'
    else:
        summary = (f'{len(times)} replies, {loss_pct}% loss, '
                   f'avg {sum(times) / len(times):.1f} ms '
                   f'(min {min(times):.1f} / max {max(times):.1f})')

    return Result('ping', 'Ping', target, ' '.join(args), ok, summary, output,
                  {'replies': len(times), 'packet_loss': loss_pct,
                   'rtt_ms': times}, elapsed)


def op_trace(target: str, **_) -> Result:
    """Trace the path and, crucially, report where it stops.

    This is the check the guide told engineers to run in a terminal. The last hop
    that answered is the closest point to the fault, which is the thing they were
    opening a terminal to find out.
    """
    if SYSTEM == 'Windows':
        args = ['tracert', '-d', '-h', str(MAX_HOPS), '-w', '1000', target]
    else:
        args = ['traceroute', '-n', '-m', str(MAX_HOPS), '-w', '2', target]

    started = time.time()
    output, _code = _run(args, TRACE_TIMEOUT)
    elapsed = int((time.time() - started) * 1000)

    hops = _parse_trace(output)
    answered = [h for h in hops if h['address']]
    last_good = answered[-1] if answered else None
    first_silent = next((h for h in hops if not h['address']), None)
    reached = bool(last_good and _same_host(last_good['address'], target))

    if reached:
        summary = f"Reached in {last_good['hop']} hops via {last_good['address']}"
    elif last_good and first_silent:
        summary = (f"Path stops after hop {last_good['hop']} ({last_good['address']}) — "
                   f"no reply from hop {first_silent['hop']} onward. Start there.")
    elif last_good:
        summary = f"Last reply from hop {last_good['hop']} ({last_good['address']})"
    else:
        summary = 'No hop answered — the first gateway is unreachable'

    return Result('trace', 'Trace path', target, ' '.join(args), reached, summary, output,
                  {'hops': hops, 'reached': reached,
                   'last_responding_hop': last_good,
                   'first_silent_hop': first_silent}, elapsed)


def _parse_trace(output: str) -> list[dict]:
    hops = []
    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r'^(\d+)\s+(.*)$', stripped)
        if not match:
            continue
        hop_no, rest = int(match.group(1)), match.group(2)
        address = None
        for candidate in re.findall(r'(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{6,})', rest):
            if re.fullmatch(r'\d+', candidate):
                continue
            address = candidate
            break
        times = [float(t) for t in re.findall(r'([\d.]+)\s*ms', rest)]
        hops.append({
            'hop': hop_no,
            'address': address,
            'rtt_ms': times,
            'timed_out': address is None,
        })
    return hops


def _same_host(address: str, target: str) -> bool:
    if address == target:
        return True
    try:
        return address in {info[4][0] for info in socket.getaddrinfo(target, None)}
    except socket.gaierror:
        return False


# ---------------------------------------------------------------- name and port


def op_dns(target: str, **_) -> Result:
    """Resolve a name. Uses the resolver directly rather than shelling out."""
    started = time.time()
    try:
        infos = socket.getaddrinfo(target, None)
        addresses = sorted({info[4][0] for info in infos})
        ok, summary = True, f"Resolves to {', '.join(addresses)}"
        output = '\n'.join(addresses)
        detail = {'addresses': addresses}
    except socket.gaierror as e:
        ok, summary = False, f'Does not resolve — {e.strerror or e}'
        output, detail, addresses = str(e), {'addresses': []}, []

    return Result('dns', 'Resolve name', target, f'nslookup {target}', ok, summary,
                  output, detail, int((time.time() - started) * 1000))


def op_reverse_dns(target: str, **_) -> Result:
    started = time.time()
    try:
        host, aliases, _ = socket.gethostbyaddr(target)
        ok, summary = True, f'{target} is {host}'
        output = '\n'.join([host, *aliases])
        detail = {'hostname': host, 'aliases': aliases}
    except (socket.herror, socket.gaierror) as e:
        ok, summary = False, f'No reverse record — {e}'
        output, detail = str(e), {'hostname': None}

    return Result('reverse_dns', 'Reverse lookup', target, f'nslookup {target}', ok,
                  summary, output, detail, int((time.time() - started) * 1000))


def op_port(target: str, port: Optional[int] = None, **_) -> Result:
    """Is the service up, not just the host? ICMP answers a different question."""
    if port is None:
        raise ValueError('Choose a port to test')

    started = time.time()
    address = target
    try:
        with socket.create_connection((target, port), timeout=5) as sock:
            address = sock.getpeername()[0]
        elapsed = int((time.time() - started) * 1000)
        ok = True
        summary = f'Port {port} is open on {address} ({elapsed} ms)'
        output = f'Connected to {address}:{port}'
    except (socket.timeout, TimeoutError):
        elapsed = int((time.time() - started) * 1000)
        ok, summary = False, f'Port {port} did not answer within 5s — filtered or dropped'
        output = summary
    except OSError as e:
        elapsed = int((time.time() - started) * 1000)
        ok, summary = False, f'Port {port} refused — {e.strerror or e}'
        output = summary

    return Result('port', 'Test port', target,
                  f'Test-NetConnection {target} -Port {port}', ok, summary, output,
                  {'port': port, 'open': ok}, elapsed)


# ---------------------------------------------------------------- local state


def op_arp(target: Optional[str] = None, **_) -> Result:
    """The ARP table answers 'is this a link problem or an address problem'."""
    args = ['arp', '-a'] + ([target] if target and SYSTEM == 'Windows' else [])
    started = time.time()
    output, _code = _run(args, DEFAULT_TIMEOUT)
    elapsed = int((time.time() - started) * 1000)

    entries = re.findall(
        r'(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})', output)
    if target:
        match = next((e for e in entries if e[0] == target), None)
        ok = match is not None
        summary = (f'{target} is at {match[1]}' if match
                   else f'No ARP entry for {target} — it has not answered on this segment')
    else:
        ok = bool(entries)
        summary = f'{len(entries)} neighbours in the ARP table'

    return Result('arp', 'ARP neighbours', target, ' '.join(args), ok, summary, output,
                  {'entries': [{'ip': ip, 'mac': mac} for ip, mac in entries]}, elapsed)


def op_routes(**_) -> Result:
    """Where this host sends traffic — the first thing to check when nothing works."""
    if SYSTEM == 'Windows':
        args = ['route', 'print', '-4']
    elif SYSTEM == 'Darwin':
        args = ['netstat', '-rn']
    else:
        args = ['ip', 'route']

    started = time.time()
    output, _code = _run(args, DEFAULT_TIMEOUT)
    elapsed = int((time.time() - started) * 1000)

    default = re.search(r'^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\S+)', output, re.M)
    if not default:
        default = re.search(r'^default via (\S+)', output, re.M)
    gateway = default.group(1) if default else None

    return Result('routes', 'Routing table', None, ' '.join(args), bool(output.strip()),
                  f'Default route via {gateway}' if gateway else 'No default route found',
                  output, {'default_gateway': gateway}, elapsed)


# ---------------------------------------------------------------- catalogue


@dataclass(frozen=True)
class Operation:
    key: str
    label: str
    description: str
    run: Callable[..., Result]
    needs_target: bool = True
    needs_port: bool = False


OPERATIONS: dict[str, Operation] = {
    op.key: op for op in (
        Operation('ping', 'Ping',
                  'Four echo requests. Is it answering, and how consistently?',
                  op_ping),
        Operation('trace', 'Trace path',
                  'Every hop between here and the target, and where the path stops.',
                  op_trace),
        Operation('port', 'Test port',
                  'Open a TCP connection. Tests the service, not just the host.',
                  op_port, needs_port=True),
        Operation('dns', 'Resolve name',
                  'What address does this name point to?',
                  op_dns),
        Operation('reverse_dns', 'Reverse lookup',
                  'What name is registered for this address?',
                  op_reverse_dns),
        Operation('arp', 'ARP neighbours',
                  'Has this address answered on the local segment at all?',
                  op_arp, needs_target=False),
        Operation('routes', 'Routing table',
                  'Where this server sends traffic, and its default gateway.',
                  op_routes, needs_target=False),
    )
}


def catalogue() -> list[dict]:
    return [
        {'key': op.key, 'label': op.label, 'description': op.description,
         'needs_target': op.needs_target, 'needs_port': op.needs_port}
        for op in OPERATIONS.values()
    ]


def run(operation: str, target: Optional[str] = None,
        port: Optional[int] = None) -> Result:
    """Run one named operation. The only entry point callers get."""
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"Unknown check '{operation}'")

    if op.needs_target:
        if not target:
            raise ValueError(f'{op.label} needs a target address or hostname')
        target = normalize_target(target)
    elif target:
        target = normalize_target(target)

    if op.needs_port:
        if port is None:
            raise ValueError(f'{op.label} needs a port number')
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('Port must be between 1 and 65535')

    return op.run(target=target, port=port)
