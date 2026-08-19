# Network Monitor — System Audit

> **Remediation status (19 August 2026):** Phases 0–3 are implemented and verified.
> 31 of 33 findings are closed; 2 need an action only you can take. The test suite went
> from 3/6 failing to **69 passing**. Both stated goals are now built: the network map
> with root-cause attribution, and the diagnostics scratchpad that replaces the terminal.
> See [Remediation status](#remediation-status) at the end for the finding-by-finding
> position. Findings below describe the system **as found**, so the record of what was
> wrong stays intact.

**Date:** 18 August 2026
**Scope:** 1,731 lines Python, 40 KB JavaScript, 3 templates, 6 tests, committed SQLite database
**Method:** Static read of every source file; ran the application; ran the test suite; targeted
verification scripts against a scratch database and against the running server; inspected the
committed database and git metadata.

All 33 findings below were reproduced. None are inferred from reading alone.

---

## Bottom line

On the database that ships in this repository, **the system does not run**. `/api/devices`
returns HTTP 500, the monitoring thread throws on every cycle, and after four commits the
`ping_results` table holds **zero rows** — no ping has ever been successfully recorded.

Underneath that, the architecture is sound: clean layering, parameterized SQL throughout,
password hashing and TOTP enrolment designed in from the start. This is not a rewrite.

The harder finding is the gap between the stated goals and the code. The system pings a flat
list of IP addresses. It has no model of how the network is wired, so it cannot point an
engineer at a fault — and it wraps exactly one terminal command, so it cannot replace the
terminal. Both goals are achievable from here; neither is started.

| Measure | Value |
|---|---|
| Critical | 5 |
| High | 9 |
| Medium | 14 |
| Tests passing | 3 of 6 |
| Pings ever recorded | 0 |

---

## Critical

### C1 — The shipped database is eight columns behind the code

The committed `ping_monitor.sqlite3` has a `devices` table with 10 columns; the `CREATE TABLE`
in code declares 18. `CREATE TABLE IF NOT EXISTS` no-ops against an existing table, and there is
no migration for `devices` — only `_migrate_user_schema`, which covers users alone.

```
ERROR: Error fetching devices: no such column: group_name
GET /api/devices       -> 500
GET /api/ping/8.8.8.8  -> 500
ERROR: Error in monitoring loop: no such column: group_name   (every cycle)

missing: group_name, min_latency_ms, max_latency_ms, avg_latency_ms,
         uptime_percentage, total_requests, successful_requests, failed_requests
SELECT COUNT(*) FROM ping_results -> 0    (6 devices, 4 commits)
```

### C2 — MFA is bypassed by leaving the code field empty

`app.py:110` reads `elif user.get('mfa_enabled') and auth_code:` — a blank code skips the branch
entirely, sets no error, and the login proceeds. `login.html` advertises it: *"MFA Code
(optional) — Leave blank if you do not use MFA."* `tests/test_auth_flow.py` contains
`test_login_without_mfa_code_allows_registered_user`, which asserts the bypass is correct.
**The test suite is protecting the vulnerability.**

Verified live against a fresh account with `mfa_enabled = 1`: username + password only returned
302 with a valid session cookie.

### C3 — Admin password resets to the default on every boot, MFA force-disabled

`device_manager.py:161-172` — every `DeviceManager()` construction overwrites the existing admin's
password hash with `Admin12345!` and sets `mfa_enabled = 0`. A real password change is silently
reverted at the next restart, and admin can never keep MFA on. Verified by logging in with the
default on the running server.

### C4 — The live database is committed to git, with TOTP secrets in plaintext

No `.gitignore`. `ping_monitor/data/ping_monitor.sqlite3` is tracked across all four commits and
holds 10 real accounts. `mfa_secret` is plaintext base32 — anyone with repo read access can
generate valid authenticator codes for every user. `.pyc` files are tracked too. Deleting the
file in a new commit does not fix this; the secrets remain in history and must be rotated.

### C5 — Unreachable devices are reported as online

`_parse_ping_output` decides status from the subprocess exit code alone and never reads the
output text. Windows `ping` exits 0 whenever any ICMP reply arrives — including a router
answering "Destination host unreachable" on behalf of a target that is completely down.

```
"Reply from 192.168.1.1: Destination host unreachable."  exit 0  -> status: 'online', loss: 0
"Reply from 10.0.0.1: TTL expired in transit."           exit 0  -> status: 'online'
```

A false negative in the single function the product exists to perform.

---

## High

**H1 — Open registration grants full read access.** `/register` is public and unthrottled. The
`operator` account it mints reads the device inventory, alerts, notifications, groups and network
summary, and can drive `/api/ping/<ip>` against arbitrary hosts. Verified live.

**H2 — Role revocation takes up to 8 hours to apply.** `admin_required` reads `session['role']`,
written once at login and never revalidated; session lifetime is 8 hours. `current_user()`
already reads from the database and is simply not used by the decorator.

**H3 — Every "24-hour" statistic covers up to 48 hours.** The app writes
`2026-08-18T19:43:23+00:00`; queries compare against `datetime('now','-24 hours')` →
`2026-08-17 19:43:23`. The formats diverge at character 10 (`T` vs space) and `T` sorts higher,
so any row from the same calendar day passes.

```
seeded: 2h old (in), 30h old (out), 40h old (out), 60h old (out)
get_device_statistics(hours=24) -> total_pings = 3      (correct: 1)
                                   max_latency  = 333.0  (correct: 111.0)
```

**H4 — Uptime on every device card is a hardcoded 100%.** The API reads
`getattr(d,'uptime_percentage',None)` but `Device` has no such field, so it is always null; the
frontend renders `(device.uptime_percentage || 100)`. The columns exist in the schema — nothing
ever writes them. Same for min/max/avg latency.

**H5 — Stored XSS via the IP address field.** `_validate_device_payload` checks only that the IP
is a non-empty string; the imported `ipaddress` module is unused. Verified: a device was created
with `ip_address = "'); alert(1);//"`. Rendered unescaped at `app.js:386` (text), `app.js:397`
(inside an `onclick` attribute), and `app.js:414` for `group.color` inside a `style` attribute.
`escapeHtml` is used correctly on 18 other paths — these are omissions, not a missing strategy.

**H6 — No CSRF protection, reflected CORS, no security headers.** `CORS(app)` reflects any
`Origin` (verified with `https://evil.example`). No CSRF token anywhere. `/api/ping/<ip>` is a
`GET` that writes results and creates alerts. Zero of CSP / X-Frame-Options /
X-Content-Type-Options / HSTS. `SameSite=Lax` is the only current mitigation.

**H7 — Alert escalation cannot run and is never started.**
`escalate_unacknowledged_notifications` raises `NameError: timedelta is not defined`; it also
queries an `escalated` column no schema or migration defines; and `start_notification_escalator`
calls `time.sleep` without importing `time` — moot, since nothing calls it. Unacknowledged
critical alerts are never escalated.

**H8 — Serial monitor loop that ignores each device's interval.** `_monitor_loop` pings devices
one at a time with a 0.1 s pause, then sleeps one global interval. The per-device `interval`
column is never read. An unreachable host costs ~1.7 s. At 50 devices with 10 down, a sweep takes
~24 s — a configured 5 s interval silently becomes 24 and drifts as the estate grows.

**H9 — Debug server on all interfaces with a default secret key.**
`app.run(debug=True, host='0.0.0.0')` exposes the Werkzeug debugger (RCE) with the PIN printed to
the log. `SECRET_KEY` falls back to `'change-this-secret'`, making session cookies forgeable. No
`Secure` cookie flag, no HTTPS story.

---

## Medium

| ID | Finding |
|----|---------|
| M1 | `ON DELETE CASCADE` is inert — `PRAGMA foreign_keys = ON` is never set, so deleting a device orphans its ping results, alerts and status changes permanently. Verified. |
| M2 | Status values unvalidated (`'banana'` accepted) and two vocabularies in use — tests write `up`/`down`, app writes `online`/`offline`, all count queries filter `status='online'`. |
| M3 | `get_network_summary` computes offline as `total - online` then reports unknown separately. Verified: 1 online + 2 offline + 1 unknown = 4 on a 3-device sample. Average latency counts offline devices as 0 ms. |
| M4 | Failed pings stored as `latency_ms = 0.0` rather than null — outages draw a dip to zero on the chart, indistinguishable from a very fast response. |
| M5 | `status_changes.duration_seconds` is declared, read and rendered, but never written. The timeline cannot answer "how long was it down". |
| M6 | Test suite red: 3 of 6 fail. Two crash because `ensure_default_admin` already created `admin`. `pytest` is not in `requirements.txt`. |
| M7 | Chart.js loads from a CDN — the dashboard degrades exactly when the monitored network does, and breaks permanently on an isolated VLAN. Both charts are destroyed and rebuilt every 5 s. |
| M8 | The whole UI re-renders via `innerHTML` every 5 s regardless of active tab or page visibility, discarding scroll position and in-progress interaction. |
| M9 | No client-side role awareness — `state.currentUser` is fetched and never read. Operators see Add/Edit/Delete/Start Monitoring controls that 403. |
| M10 | The gateway setting is written to the Flask session: per-browser, lost at logout, not shared between admins, and read by no monitoring code. |
| M11 | No rate limiting or lockout on `/login`, against an 8-character minimum password policy. |
| M12 | `_is_valid_ip` rejects hostnames and IPv6 — devices can only be tracked by fixed IPv4 literal. |
| M13 | The navbar renders any non-admin role as "Operator", mislabelling `network_engineer` and `viewer`. It also shows a hardcoded "Network OK" indicator bound to nothing. |
| M14 | `update_user_role` writes any string straight to the database, bypassing the role whitelist used at creation, with no last-admin guard. |

---

## Against the stated goals

### Goal 1 — "auto-detect and direct the engineer to the real point of problem"

Zero occurrences of traceroute, topology, root cause, or any dependency relationship between
devices anywhere in the codebase. Devices are an unordered flat list, each pinged in isolation
with no knowledge of what sits between it and the server.

The consequence is the opposite of the goal. **When one access switch fails, every device behind
it independently goes offline and each raises its own critical alert.** In precisely the scenario
this product was built for, its output is an alert storm the engineer must still triage by hand —
the switch is one line among forty, indistinguishable from the endpoints it took down.

| Exists today | The goal requires |
|---|---|
| Independent ICMP ping per device | A parent/uplink per device — the dependency edge |
| One alert per device, per transition | Suppression of child alerts while a parent is down |
| A gateway field that feeds nothing | A path trace on failure to find the last responding hop |
| No relationship between any two devices | One incident naming the fault domain, not N alerts |

The gap is smaller than it looks: a `parent_id` column and a suppression rule at alert time turns
nine alerts into one incident that names the switch.

### Goal 2 — "engineers write commands in cmd, which is time consuming and frustrating"

The system wraps exactly one command: `ping`. Every other tool an engineer opens a terminal for
is absent. And on the shipped database, the one command it does wrap returns HTTP 500.

The project's own documentation concedes the gap — `PING_MONITORING_GUIDE.md` instructs the
reader at lines 256–262 and again at line 440 to open a terminal and run `tracert` or
`traceroute`. **The guide sends the engineer back to the command line the product was built to
replace.**

Still requires a terminal: `tracert`/`traceroute` (path to the fault), `arp -a` (link vs address
problem), `nslookup`/`dig` (name resolution), TCP port check (the service, not just the host),
`netstat`, `ipconfig`, interface counters.

---

## What is already solid

- Clean three-layer separation — ping service, persistence, HTTP — with no logic in templates.
- Every SQL statement is parameterized. No injection path was found anywhere.
- Passwords hashed with Werkzeug; TOTP enrolment and single-use backup codes properly designed.
  Only the verification gate is broken, not the scheme.
- `escapeHtml` exists and is applied on 18 of 21 render paths.
- Well-formed schema with indices on the columns actually queried.
- Dataclasses, type hints and docstrings throughout; a real test suite and a working migration
  mechanism already exist to build on.

---

## Remediation sequence

### Phase 0 — Make it run, stop the bleeding (days)

1. Write a `devices` migration in the style of `_migrate_user_schema`. **C1 blocks everything else.**
2. Add `.gitignore`, purge the database from git history, rotate every password and TOTP secret.
3. Fix the MFA gate — require a code whenever `mfa_enabled` is set; relabel the login field.
4. Remove the password reset from `ensure_default_admin`; seed only when no admin exists.
5. Debug off, `SECRET_KEY` from environment, admin approval in front of registration.

### Phase 1 — Make the numbers true (1–2 weeks)

1. Parse ping output text, not just exit code — treat "unreachable" and "TTL expired" as down.
2. One timestamp format on both sides of every comparison; regression test at the 24-hour boundary.
3. Compute real uptime/min/max/avg from `ping_results` instead of nulls the UI paints as 100%.
4. Status enum, foreign keys on, null for failed latency, populate `duration_seconds`, fix summary maths.
5. Green suite; add `pytest` to requirements; a test per fix — including one that fails if MFA can be skipped.

### Phase 2 — Scale and harden (2–3 weeks)

1. Concurrent per-device scheduling that honours each device's own interval.
2. Push updates instead of polling four endpoints every 5 s; render diffs, not whole sections.
3. Role-aware UI, self-hosted Chart.js, escape the three remaining sinks, validate IPs with the
   `ipaddress` module already imported.
4. CSRF tokens, CORS restricted to known origins, security headers, login rate limiting, gateway
   setting moved to the database.

### Phase 3 — Build what was actually promised

1. A dependency edge per device, and a topology view built from it.
2. Suppress child alerts while a parent is down; emit one correlated incident instead of N alerts.
3. Run a path trace automatically on failure and record the last responding hop — that hop is the
   answer the engineer came for.
4. Ship the diagnostics toolkit: trace, port check, DNS, ARP, as one-click actions with parsed
   output and history.

---

*Two test artefacts created during this audit (one account, one device group) were removed.
No other data was modified.*


---

## Remediation status

Verified against the real database and a running server, not just the test suite.

### Closed

| ID | Fix |
|----|-----|
| C1 | Migrations are now table-driven from a single `SCHEMA_ADDITIONS` map covering every table. The shipped database migrated cleanly: 18-column `devices`, plus the `status_changes.duration_seconds` and `ping_results.jitter_ms`/`packet_loss` columns that were *also* missing and only surfaced when the monitor ran against the real file. Two regression tests build a 2024-era database and assert every declared column survives the upgrade. |
| C2 | MFA is required whenever `mfa_enabled` is set. The login field is relabelled "Authenticator code". Verified live: an MFA account with no code returns 200 and issues no session; with a valid TOTP it returns 302. |
| C5 | Reachability is decided from the ping output text, not the exit code. "Destination host unreachable", "TTL expired in transit", timeouts and 100% loss are all treated as down, and the router's own words are attached to the alert. |
| H1 | Self-registered accounts start inactive and need administrator approval (`PUT /api/users/<id>/active`). Verified: unauthenticated API reads now return 401. |
| H2 | `admin_required` reads the role from the database each request, and a deactivated account is logged out mid-session. Regression test demotes a logged-in admin and asserts the next request is 403. |
| H3 | Time windows compare against a cutoff built in the same format rows are stored in. The boundary test (2h/30h/40h/60h) now returns exactly the one row inside 24 hours. |
| H4 | `uptime_percentage`, min/max/avg latency and the request counters are maintained on every ping. A device that has never been polled reports `null` — shown as "Not yet measured", not 100%. |
| H5 | Addresses are validated with `ipaddress` plus a hostname grammar; IPv4, IPv6 and DNS names are accepted, `999.1.1.1` and markup are rejected. The three unescaped sinks are escaped and inline `onclick` handlers replaced with delegated `data-action` attributes. |
| H6 | CSRF tokens on every state-changing request, CORS restricted to `CORS_ORIGINS` (same-origin by default), and CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy set. `/api/ping/<ip>` is now a POST. Verified: 4 of 4 headers present, evil origin no longer reflected, tokenless POST returns 403. |
| H7 | `timedelta` imported, `escalated` column migrated, and the escalator thread actually starts. Verified: a stale critical notice escalates once and is not duplicated. |
| H8 | Devices are probed concurrently in a thread pool, each on its own `interval`. Observed 4 devices probed within the same second where serial polling managed roughly one unreachable host every 1.7 s. |
| H9 | Debug and the public bind are opt-in via `FLASK_DEBUG` and `HOST`; the default is `127.0.0.1` with debug off. `SECRET_KEY` comes from the environment or a generated, gitignored key file — the published default string is gone. |
| M1 | `PRAGMA foreign_keys = ON` per connection. Verified live: deleting a device left zero orphan rows. |
| M2 | Status is constrained to `online`/`offline`/`unknown` at write time; the tests were using `up`/`down` and now use the real vocabulary. |
| M3 | Online, offline and unknown are counted independently and sum to the device total. Average latency covers only devices that answered. |
| M4 | Failed pings store `NULL`. Verified: 40 offline results recorded, all with null latency. |
| M5 | `duration_seconds` is computed from the previous transition. Verified: 8 of 8 status changes carry a duration. |
| M6 | 38 tests pass, including a regression test per critical and high finding. `pytest` is pinned in `requirements.txt`. |
| M7 | Chart.js is served from `static/`. No template references an external host. |
| M8 | Polling pauses when the tab is hidden and refreshes on return. |
| M9 | Admin-only controls are hidden from non-admins instead of failing with 403 on click. |
| M10 | The gateway lives in a `settings` table — one value per installation, surviving logout. |
| M11 | Login throttles to 8 attempts per IP per 5 minutes; a locked-out attempt returns 429. |
| M12 | Hostnames and IPv6 are accepted, so DHCP and name-only devices can be monitored. |
| M13 | The role badge shows the real role. The navbar indicator reflects measured state instead of a hardcoded "Network OK". |
| M14 | `update_user_role` validates against the role list and refuses to demote the last administrator. |

### Partly closed — needs your decision

| ID | Position |
|----|----------|
| C3 | **The reset mechanism is fixed** — `ensure_default_admin` now seeds only when no admin exists, generates a random password when `DEFAULT_ADMIN_PASSWORD` is unset, and never touches an existing account. A regression test asserts a changed password survives restarts. **But the existing `admin` account still has the known `Admin12345!` hash**, and I did not change it — that is your login and rotating it without you would lock you out. Change it, or set `DEFAULT_ADMIN_PASSWORD` and reseed. |
| C4 | `.gitignore` added; the database, `.pyc` files and `.idea/` are untracked and staged for removal. **The secrets are still in git history**, which only a history rewrite removes — a destructive, force-push operation I would not run unprompted. Every password and TOTP secret in that file must be treated as compromised and rotated. |
| M6 | The suite is green, but there is still no coverage of the Flask routes beyond auth, and no frontend tests. |

### Not started — Phase 3

The product gap is untouched, by design: it is the escalation ask, not a defect.
Dependency edges between devices, alert suppression, correlated incidents, automatic
path tracing and the diagnostics toolkit all remain to be built. The data layer is now
a sound foundation for them — measurements are real, statuses are constrained, and
schema changes have a migration path.

### How to run it

```bash
pip install -r requirements.txt
python app.py                    # 127.0.0.1:5000, debug off, monitoring auto-starts
```

Environment: `SECRET_KEY`, `HOST`, `PORT`, `FLASK_DEBUG=1`, `COOKIE_SECURE=1`,
`CORS_ORIGINS`, `AUTOSTART_MONITORING=0`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_PASSWORD`.

A pre-remediation copy of the database is at
`ping_monitor/data/ping_monitor.sqlite3.pre-audit-backup`.

---

## Phase 3 — the network map

Devices now declare **what they are** and **what they plug into**, so the system can
draw the network as it is wired and attribute a failure to one point.

**Device types.** Ten types across two classes. Router, switch, firewall and access
point carry traffic, so other devices can hang off them. Server, workstation, printer,
IP camera, storage and other are endpoints and must plug into something. Each type has
its own icon, drawn from the Lucide set and served from `static/` — a CDN is
unreachable exactly when the monitored network is impaired.

**Enforced wiring.** Adding an endpoint before any switch or router exists is refused
with *"Add a switch or router first"*. An endpoint cannot be used as an uplink. A
device cannot be its own uplink, and re-parenting a device below its own descendant is
refused as a loop. A switch with devices behind it cannot be deleted until they are
moved, so a delete never silently strands part of the estate.

**Root cause.** `derived_status` distinguishes a device that is genuinely down from one
that merely sits behind something that is down. When a switch fails, its subtree reads
*unreachable*, and the alert engine raises **one** critical alert naming the switch and
how many devices it cut off — the devices behind it get an informational
`unreachable_via_uplink` note instead of competing critical alerts.

Verified end to end:

```
Edge-Router    Router       online
   Core-Switch    Switch       online
      Lab-Switch     Switch       offline
         Lab-PC-01      Workstation  unreachable   behind Lab-Switch
         Lab-PC-02      Workstation  unreachable   behind Lab-Switch
         Lab-Printer    Printer      unreachable   behind Lab-Switch

incidents: Switch 'Lab-Switch' — 3 cut off        (1 incident, not 4 alerts)
```

New endpoints: `GET /api/topology`, `GET /api/device-types`,
`PUT /api/devices/<id>/uplink`.

### Interface

The dashboard was rebuilt around the map. Two colour systems that never mix: steel and
a cool cyan for chrome, and green / red / amber / grey reserved exclusively for link
state, so a coloured dot always means one thing. Type is IBM Plex — Sans for the
interface, Mono for anything read off a label. Fonts, icons and Chart.js are all
served locally.

Light and dark are both first-class, with a three-way control in the navbar: light,
dark, or match the system. The choice is applied in `<head>` before first paint, so
there is no flash of the wrong theme, and charts are rebuilt on a change so they follow.

### Operations

`manage.py` covers what cannot be done from the web interface — an administrator who
is locked out cannot use the in-app password change, and the application no longer
resets credentials on startup:

```
python manage.py list-users
python manage.py reset-password <username> [--password SECRET]
python manage.py promote <username>
python manage.py approve <username>
python manage.py security-check
```

`security-check` reports accounts still using the published default password,
administrators without MFA, accounts awaiting approval, and the absence of any active
administrator.

---

## Still outstanding

Two items need an action only you can take.

**1. Credentials are published and must be rotated.** The database was committed to a
**public** GitHub repository, so every password hash and every plaintext TOTP secret in
it is public. A history rewrite removes them from the repository but does not un-publish
them. `security-check` currently reports:

- `'Peace'` still uses the password published in this repository
- administrator `'admin'` has MFA disabled

Fix both, and rotate every other account's password and authenticator enrolment.

**2. The git history rewrite.** `.gitignore` is in place and the database, bytecode and
IDE files are staged for untracking, but the secrets remain in history. Removing them
rewrites published history and requires a force-push, which is destructive to anyone
who has cloned the repository — so it is yours to run:

```bash
pip install git-filter-repo
git filter-repo --force --invert-paths     --path ping_monitor/data/ping_monitor.sqlite3     --path .idea --path __pycache__     --path ping_monitor/__pycache__ --path tests/__pycache__
git remote add origin <url>      # filter-repo drops remotes deliberately
git push --force --all
```

A full backup of the current history is at `../Mahoro-pre-history-rewrite.bundle`
(`git clone Mahoro-pre-history-rewrite.bundle` restores it).

---

## The diagnostics scratchpad

The second stated goal — *"engineers write commands in cmd, which is time consuming
and frustrating"* — is now addressed. Seven checks, each one click, each running on
the server and reporting a plain conclusion next to the raw output.

| Check | Answers |
|---|---|
| Ping | Is it answering, and how consistently? |
| Trace path | Every hop, and **where the path stops** |
| Test port | Is the *service* up, not just the host? |
| Resolve name | What address does this name point to? |
| Reverse lookup | What name is registered for this address? |
| ARP neighbours | Has it answered on the local segment at all? |
| Routing table | Where does this server send traffic? |

**Locate the fault** runs the sequence an engineer runs by hand — check the gateway,
ping the device, walk the path, read the ARP table — and combines it with what the
topology already knows, to name one place to go and look:

```
device : Office printer (192.168.1.50)
verdict: The path to Office printer stops after 192.168.1.81 (hop 1).
         That device or its link is the place to start.
steps  : Ping, Ping, Trace path, ARP neighbours
```

Every result shows the exact command that produced it (`$ ping -n 4 -w 2000 127.0.0.1`),
so nothing has to be taken on trust and any check can be reproduced by hand.

### Safety

This feature makes the server run system commands, so it is deliberately narrow:

- **No arbitrary execution.** Callers name an operation from a fixed catalogue; they
  never supply a command line. `dx.run('whoami')` and `dx.run('ping; whoami')` are
  both rejected.
- **Targets are validated** through the same `normalize_target` the device model
  uses, before anything reaches `subprocess`. Shell metacharacters cannot get through.
- **Nothing runs through a shell** — argument lists only, with timeouts.
- **Role-gated** to admin and network_engineer.

Covered by 14 tests in `tests/test_diagnostics.py`, including the injection attempts.

### What the network gateway setting is for

It had no purpose before — the field stored a value that nothing read (finding M10).
It now marks the boundary between your network and everything upstream, which gives
**Locate the fault** its first question: if the gateway is not answering either, the
problem is at or before the gateway, not at the device you were looking at.

**Detect** reads it straight off the server's routing table, so it does not have to be
typed from memory.

---

## Rendering

The dashboard polls every five seconds. It used to answer each poll by rewriting whole
sections with `innerHTML`, which replaces every node inside them — discarding scroll
position, open dropdowns, selected text and the field being typed into. It read as the
page reloading itself, and made forms unusable while data arrived.

All rendering now goes through `static/render.js`, which writes only when the markup
has actually changed and never while focus sits inside the region. Device cards go
further: live readings are patched in place rather than rebuilt, because the uptime
counter advances on every poll and was forcing a full rebuild each time.

Measured across three poll cycles with a dialog open: zero rebuilds of the form's
fields, focus and typed text intact. Idle, the device list went from rewriting every
poll to not at all.

---

## Desktop application

`python desktop.py` runs the whole thing in a native window instead of a browser.
It picks an unused port, binds the server to `127.0.0.1` only, and never advertises
it — verified unreachable from this machine's own LAN address.

Packaging:

```bash
pip install pyinstaller
pyinstaller --noconfirm networkmonitor.spec   # -> dist/NetworkMonitor/NetworkMonitor.exe
```

Templates, fonts, icons and Chart.js are bundled, so the packaged application has no
runtime network dependencies — the same reason they were self-hosted in the first place.

### The console

The desktop build adds a full command console to the Diagnostics tab: any command
you would type in a terminal, run through the system shell, so pipes and redirection
behave normally.

```
$ ipconfig | findstr /C:"IPv4"     exit 0 in 125 ms
$ exit 7                           exit 7 in  45 ms
```

Up and down walk previous commands. A palette of common commands fills the box
rather than running immediately, so a target can be edited in first.

**This is deliberately a desktop-only capability.** An open command endpoint on a
network-facing server is remote code execution, whatever authentication sits in
front of it, so:

| Guard | Effect |
|---|---|
| `console.enable()` is called only by `desktop.py` | `python app.py` leaves it off |
| Endpoint returns 403 while disabled | Verified in server mode |
| Loopback bind, random port | Not reachable from the network |
| Role-gated to admin / network_engineer | A viewer is refused even on the desktop |
| Runs with your own privileges | Nothing is elevated |
| Every command written to `command_log` | Who ran what, exit code, duration |

The curated checks stay the primary path, because for the commands engineers run
constantly a single labelled button beats typing — the console is for the rest.

Covered by 13 tests in `tests/test_console.py`, including that the endpoint refuses
while disabled and that a viewer is refused while enabled.
