"""Operational commands for the network monitor.

Administrator recovery has to exist outside the web interface: an admin who is
locked out cannot use the in-app password change, and the application no longer
resets credentials on startup (which is what previously made a lockout impossible
and every installation share one published password).

    python manage.py list-users
    python manage.py reset-password <username> [--password SECRET]
    python manage.py promote <username>
    python manage.py approve <username>
    python manage.py security-check
"""
import argparse
import secrets
import sys

from werkzeug.security import generate_password_hash

from ping_monitor.device_manager import DeviceManager


def cmd_list_users(dm: DeviceManager, args) -> int:
    users = dm.list_users()
    if not users:
        print("No accounts.")
        return 0
    print(f"{'id':>4}  {'username':<20}{'role':<18}{'mfa':<6}{'active':<8}last login")
    for u in users:
        print(f"{u['id']:>4}  {u['username']:<20}{u['role']:<18}"
              f"{'on' if u['mfa_enabled'] else 'off':<6}"
              f"{'yes' if u.get('is_active', 1) else 'no':<8}"
              f"{u['last_login_at'] or 'never'}")
    return 0


def cmd_reset_password(dm: DeviceManager, args) -> int:
    user = dm.get_user_by_username(args.username)
    if user is None:
        print(f"No account named '{args.username}'.", file=sys.stderr)
        return 1

    password = args.password or secrets.token_urlsafe(15)
    if len(password) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        return 1

    with dm._connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user['id']),
        )
        conn.commit()

    print(f"Password reset for '{args.username}'.")
    if not args.password:
        print(f"\n    {password}\n")
        print("Shown once. Store it in a password manager, or sign in and change it.")
    return 0


def cmd_promote(dm: DeviceManager, args) -> int:
    user = dm.get_user_by_username(args.username)
    if user is None:
        print(f"No account named '{args.username}'.", file=sys.stderr)
        return 1
    dm.update_user_role(user['id'], 'admin')
    dm.set_user_active(user['id'], True)
    print(f"'{args.username}' is now an active administrator.")
    return 0


def cmd_approve(dm: DeviceManager, args) -> int:
    user = dm.get_user_by_username(args.username)
    if user is None:
        print(f"No account named '{args.username}'.", file=sys.stderr)
        return 1
    dm.set_user_active(user['id'], True)
    print(f"'{args.username}' can now sign in.")
    return 0


def cmd_security_check(dm: DeviceManager, args) -> int:
    """Report the credential problems an operator has to fix by hand."""
    problems = []

    for user in dm.list_users():
        if dm.uses_default_password(user['username']):
            problems.append(
                f"'{user['username']}' still uses the password published in this repository"
            )
        if user['role'] == 'admin' and not user['mfa_enabled']:
            problems.append(f"administrator '{user['username']}' has MFA disabled")
        if not user.get('is_active', 1):
            problems.append(f"'{user['username']}' is registered but not yet approved")

    if dm.count_admins() == 0:
        problems.append("no active administrator — run: python manage.py promote <username>")

    if not problems:
        print("No credential problems found.")
        return 0

    print(f"{len(problems)} issue(s):")
    for problem in problems:
        print(f"  - {problem}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('list-users', help='show every account')

    reset = sub.add_parser('reset-password', help='set a new password for an account')
    reset.add_argument('username')
    reset.add_argument('--password', help='use this instead of a generated one')

    promote = sub.add_parser('promote', help='make an account an active administrator')
    promote.add_argument('username')

    approve = sub.add_parser('approve', help='approve a self-registered account')
    approve.add_argument('username')

    sub.add_parser('security-check', help='report credential problems')

    args = parser.parse_args()
    dm = DeviceManager()

    handlers = {
        'list-users': cmd_list_users,
        'reset-password': cmd_reset_password,
        'promote': cmd_promote,
        'approve': cmd_approve,
        'security-check': cmd_security_check,
    }
    try:
        return handlers[args.command](dm, args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
