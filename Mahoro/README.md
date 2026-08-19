# Network Monitor

A monitoring tool for small and mid-sized networks. It watches the devices you care
about, draws the network as it is actually wired, and — when something breaks — tells
you **which device to go and look at** rather than handing you forty alerts.

It also puts the commands an engineer normally types into a terminal behind one click.

---

## What it does

**Watches devices.** Each device is polled on its own schedule. Reachability is read
from the ping output rather than the exit code, so a router answering "destination
host unreachable" on behalf of a dead device is recorded as *down*, not up.

**Draws the network.** Every device declares what it is (router, switch, firewall,
access point, server, workstation, printer, office telephone, IP camera, storage) and
what it plugs into. That uplink is what turns a list of addresses into a map.

**Names the fault.** When a switch fails, everything behind it stops answering too.
Instead of one critical alert per affected device, you get one incident naming the
switch and how many devices it cut off; the rest are marked *cut off* and attributed
to it.

```
Edge-Router      online
  Core-Switch      online
    Lab-Switch       offline      <- 1 incident, "3 devices cut off"
      Lab-PC-01        cut off, behind Lab-Switch
      Lab-PC-02        cut off, behind Lab-Switch
      Lab-Printer      cut off, behind Lab-Switch
```

**Replaces the terminal for routine checks.** Seven one-click diagnostics — ping,
trace path, TCP port test, DNS, reverse DNS, ARP neighbours, routing table — each
showing a plain conclusion, the raw output, and the exact command that produced it.
*Locate the fault* runs the whole sequence and reports the closest point to the problem.

**Runs as a desktop application,** where it also gains a full command console.

---

## Requirements

- **Python 3.9 or newer**
- Windows, macOS or Linux
- `ping`, `tracert`/`traceroute` and `arp` available on the host (standard everywhere)

No internet connection is needed at runtime. Fonts, icons and charting are served from
the application itself, deliberately: a tool for diagnosing network failure should not
lose half its interface when the network fails.

---

## Install

```bash
git clone https://github.com/sezeranoplacide/networking-ping-monitoring-sysytem.git
cd networking-ping-monitoring-sysytem/Mahoro

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Run it

Either form works. The desktop application is the one that includes the console.

```bash
python app.py        # web server at http://127.0.0.1:5000
python desktop.py    # native window, private port, console enabled
```

### First sign-in

On first start the application creates an administrator and generates a random
password, then writes it beside the database:

```
ping_monitor/data/first-run-password.txt
```

The sign-in page points at that file. Change the password once you are in — the file
is deleted automatically when you do.

To choose the password yourself instead, set `DEFAULT_ADMIN_PASSWORD` before the first
run and no file is written.

### First steps in the app

1. **Devices → Add device.** Start with a router or switch; endpoints have to plug
   into something, so the form will not accept a workstation until an uplink exists.
2. Add the rest, choosing the switch each one connects to. The map builds itself.
3. **Dashboard → Network Map** shows the result, colour-coded by state.
4. **Diagnostics** is where the checks live. Set the gateway on the Dashboard
   (or press **Detect** to read it from the routing table) so *Locate the fault* can
   tell an internal problem from an upstream one.

---

## Build a desktop executable

Produces a folder that runs on a machine with no Python installed.

```bash
pip install pyinstaller
pyinstaller --noconfirm --clean networkmonitor.spec
```

Result: `dist/NetworkMonitor/NetworkMonitor.exe` (~33 MB with its `_internal` folder).
Distribute the **whole `dist/NetworkMonitor` folder** — the executable will not start
without it.

The packaged application keeps its data outside the bundle, so upgrades and reinstalls
do not lose it:

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\NetworkMonitor` |
| macOS | `~/Library/Application Support/NetworkMonitor` |
| Linux | `~/.local/share/NetworkMonitor` |

Set `NETMON_DATA_DIR` to override, which is also how you make a portable install.

---

## Configuration

All optional; the defaults are safe for local use.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | generated and persisted | Signs session cookies. **Set this for any shared deployment.** |
| `HOST` | `127.0.0.1` | Interface to bind. `0.0.0.0` exposes it to the network. |
| `PORT` | `5000` | Port to listen on. |
| `FLASK_DEBUG` | `0` | `1` enables the debugger. Never on a reachable host. |
| `COOKIE_SECURE` | `0` | `1` once you are serving over HTTPS. |
| `CORS_ORIGINS` | *(none)* | Comma-separated origins allowed to call the API. Same-origin only by default. |
| `AUTOSTART_MONITORING` | `1` | `0` to start without polling. |
| `DEFAULT_ADMIN_USERNAME` | `admin` | Name of the seeded administrator. |
| `DEFAULT_ADMIN_PASSWORD` | *(generated)* | Set it to choose the first password yourself. |
| `NETMON_DATA_DIR` | see above | Where the database and secret key are kept. |

### Serving it to other people

The development server Flask ships with is not meant for that. Put a real WSGI server
in front of it and terminate TLS:

```bash
pip install waitress
waitress-serve --host 0.0.0.0 --port 8000 app:app
```

Set `SECRET_KEY` and `COOKIE_SECURE=1`, and note that the command console stays
disabled in this mode by design — see **Security** below.

---

## Accounts

Four roles:

| Role | Can |
|---|---|
| `admin` | Everything, including managing devices, groups and users |
| `network_engineer` | View everything, run diagnostics and the console |
| `operator` | View everything |
| `viewer` | View everything |

Registration is open, but a new account **cannot sign in until an administrator
approves it** (Users tab). Multi-factor authentication is on by default for
self-registered accounts; a secret and a single-use backup code are shown once at
registration.

### Administration from the command line

For anything the interface cannot do — including recovering a locked-out administrator:

```bash
python manage.py list-users
python manage.py reset-password <username> [--password SECRET]
python manage.py promote <username>
python manage.py approve <username>
python manage.py security-check
```

`security-check` reports accounts still using a published default password,
administrators without MFA, accounts awaiting approval, and the absence of any active
administrator.

---

## Project layout

```
Mahoro/
├── app.py                     HTTP routes, auth, CSRF, security headers
├── desktop.py                 Native-window launcher (enables the console)
├── manage.py                  Command-line administration
├── networkmonitor.spec        PyInstaller build definition
├── requirements.txt
│
├── ping_monitor/
│   ├── device_manager.py      SQLite persistence, topology, root-cause attribution
│   ├── ping_service.py        Concurrent polling, alerting, alert suppression
│   ├── diagnostics.py         The seven checks — allow-listed, no shell
│   ├── console.py             Free-form command console (desktop only)
│   └── paths.py               Where resources and user data live
│
├── templates/                 login, register, dashboard
├── static/
│   ├── app.js                 Dashboard behaviour
│   ├── topology.js            Network map and device icons (Lucide, bundled)
│   ├── diagnostics.js         Diagnostics and console
│   ├── render.js              Guarded rendering — see "Why the UI is quiet"
│   ├── theme.js               Light / dark / follow-system
│   ├── style.css, plex.css, fonts/, chart.umd.min.js
│
└── tests/                     85 tests
```

### Why the UI is quiet

The dashboard polls every five seconds, but rendering goes through `render.js`, which
writes to the page only when the markup has actually changed and never while your
cursor is in that part of the interface. Live readings on device cards are patched in
place. Without this, a five-second poll throws away scroll position, open dropdowns and
whatever you were typing.

---

## API

All endpoints require a signed-in session. Anything that changes state needs an
`X-CSRF-Token` header, obtainable from `GET /api/auth/status`.

**Devices**

```
GET    /api/devices                      list, with live status and uptime
POST   /api/devices                      create (admin)
GET    /api/devices/<id>
PUT    /api/devices/<id>                 rename, retype, re-home, regroup (admin)
DELETE /api/devices/<id>                 refused while devices hang off it (admin)
PUT    /api/devices/<id>/uplink          move it on the map (admin)
POST   /api/devices/<id>/assign-group    (admin)
POST   /api/ping/<address>               probe now and record the result
```

**Topology**

```
GET    /api/topology       nodes, derived status, blocked_by, incidents
GET    /api/device-types   the types, and which can carry traffic
```

**Diagnostics** *(admin or network_engineer)*

```
GET    /api/diagnostics                 available checks and suggested targets
POST   /api/diagnostics/run             { operation, target, port }
POST   /api/diagnostics/locate/<id>     the full fault-finding sequence
POST   /api/diagnostics/detect-gateway  read the default route off this host
```

**Console** *(desktop application only)*

```
GET    /api/console            availability and the command palette
POST   /api/console/run        { command }
GET    /api/console/history    the audit log
```

**Monitoring, analytics, alerts**

```
GET    /api/network/summary            counts, health, average latency
GET    /api/devices/<id>/statistics    metrics over a time window
GET    /api/devices/<id>/history       ping history
GET    /api/devices/<id>/timeline      status transitions, with durations
GET    /api/alerts                     ?unacknowledged=true to filter
POST   /api/alerts/<id>/acknowledge
GET    /api/notifications
POST   /api/notifications/<id>/ack
POST   /api/monitoring/start|stop      (admin)
GET    /api/monitoring/status
```

**Groups, users, settings**

```
GET    /api/groups
POST   /api/groups                       (admin)
GET    /api/users                        (admin)
PUT    /api/users/<id>/role              (admin)
PUT    /api/users/<id>/active            approve or suspend (admin)
PUT    /api/account/password             change your own
GET    /api/account/security             warnings you need to act on
GET/POST /api/settings/network-gateway   (admin)
```

---

## Data

SQLite, at `ping_monitor/data/ping_monitor.sqlite3` from a checkout, or in the
per-user directory shown above for a packaged build.

| Table | Holds |
|---|---|
| `devices` | Configuration, current status, uptime counters, uplink |
| `ping_results` | Every probe; a failure stores `NULL` latency, not `0` |
| `status_changes` | Transitions, with how long the previous state held |
| `alerts` / `notifications` | Alerts and the escalation feed |
| `device_groups` | Groupings |
| `users` | Accounts, roles, MFA enrolment, approval state |
| `settings` | Installation-wide settings such as the gateway |
| `command_log` | Console audit trail |

Schema upgrades are automatic. Columns added after a table first shipped are declared
in `SCHEMA_ADDITIONS`, and applied on start — so an older database keeps working.

**The database contains password hashes and MFA secrets. Never commit it.** It is
already in `.gitignore`.

---

## Security

- Sessions are signed, `HttpOnly`, `SameSite=Lax`, and roles are re-read from the
  database on every request, so revoking access takes effect immediately.
- CSRF tokens on every state-changing request; CORS is same-origin unless configured.
- `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options` and
  `Referrer-Policy` are set on every response.
- Sign-in is throttled to 8 attempts per address per 5 minutes.
- Diagnostics never build a command from user input: callers name an operation from a
  fixed list, and targets are validated before reaching `subprocess`.
- **The console runs only in the desktop application.** An endpoint that runs arbitrary
  commands on a network-reachable server is remote code execution, whatever
  authentication sits in front of it, so `python app.py` leaves it switched off. In the
  desktop application it runs with your own privileges — nothing is elevated — and
  every command is written to `command_log`.

`SYSTEM_AUDIT.md` documents the full review this codebase went through, what was found,
and what was fixed.

---

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests -q
```

85 tests. Alongside ordinary coverage they pin the defects that were found and fixed —
the MFA bypass, the false "online" on unreachable hosts, the 24-hour window that
covered 48, and the schema drift that made an older database unusable.

---

## Troubleshooting

**Port already in use** — `PORT=8080 python app.py`. The desktop application picks a
free port by itself.

**Everything shows as "Not yet polled"** — monitoring has not started. Start it from
the Dashboard, or check `AUTOSTART_MONITORING`.

**A device is down but you expect it up** — it may only be blocking ICMP. Use
**Test port** in Diagnostics to check the service directly.

**Locked out** — `python manage.py reset-password <username>`.

**Start over** — delete the database; it is recreated with a fresh administrator on the
next run.

**The interface looks stale after an update** — pages are sent with `no-store` and
assets are versioned, so a normal reload is enough. If it persists, the server is
probably still running old code; restart it.

---

## License

MIT
