from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

import app as flask_app
from ping_monitor.device_manager import DeviceManager


@pytest.fixture()
def client(tmp_path: Path):
    db_path = tmp_path / "ping_monitor.sqlite3"
    manager = DeviceManager(db_path=str(db_path))
    flask_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    flask_app.dm = manager

    with flask_app.app.test_client() as test_client:
        yield test_client, manager


def csrf(test_client) -> str:
    """Fetch a CSRF token the way the browser does — by loading a page first."""
    test_client.get("/login")
    with test_client.session_transaction() as session:
        return session["csrf_token"]


def login(test_client, username: str, password: str, **extra):
    data = {"username": username, "password": password, "csrf_token": csrf(test_client)}
    data.update(extra)
    return test_client.post("/login", data=data, follow_redirects=False)


def make_user(manager: DeviceManager, username: str, **kwargs) -> dict:
    kwargs.setdefault("password", "Password123")
    kwargs.setdefault("role", "operator")
    kwargs.setdefault("mfa_enabled", False)
    return manager.create_user(username=username, **kwargs)


# ---------------------------------------------------------------- authentication


def test_mfa_enabled_account_cannot_log_in_without_a_code(client) -> None:
    """Regression for the MFA bypass: a blank code used to skip the check entirely."""
    test_client, manager = client
    make_user(manager, "engineer1", mfa_enabled=True)

    response = login(test_client, "engineer1", "Password123")

    assert response.status_code == 200  # re-renders the form, does not redirect
    with test_client.session_transaction() as session:
        assert "user_id" not in session


def test_mfa_enabled_account_logs_in_with_a_valid_code(client) -> None:
    import pyotp

    test_client, manager = client
    created = make_user(manager, "engineer2", mfa_enabled=True)
    code = pyotp.TOTP(created["mfa_secret"]).now()

    response = login(test_client, "engineer2", "Password123", auth_code=code)

    assert response.status_code == 302
    with test_client.session_transaction() as session:
        assert session["user_id"] == created["id"]


def test_account_without_mfa_logs_in_normally(client) -> None:
    test_client, manager = client
    make_user(manager, "operator1")

    response = login(test_client, "operator1", "Password123")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/home")


def test_inactive_account_is_refused(client) -> None:
    test_client, manager = client
    make_user(manager, "pending1", is_active=False)

    login(test_client, "pending1", "Password123")

    with test_client.session_transaction() as session:
        assert "user_id" not in session


def test_repeated_failures_are_throttled(client) -> None:
    test_client, manager = client
    make_user(manager, "target1")

    for _ in range(flask_app.LOGIN_MAX_ATTEMPTS):
        login(test_client, "target1", "WrongPassword")

    response = login(test_client, "target1", "Password123")
    assert response.status_code == 429
    flask_app.clear_failed_logins()


# ---------------------------------------------------------------- default admin


def test_existing_admin_password_survives_restart(client) -> None:
    """Regression: ensure_default_admin used to reset the password on every boot."""
    _, manager = client
    admin = manager.get_user_by_username("admin")
    assert admin is not None, "a first administrator should be seeded"
    original_hash = admin["password_hash"]

    manager.ensure_default_admin()
    manager.ensure_default_admin()

    assert manager.get_user_by_username("admin")["password_hash"] == original_hash
    assert not check_password_hash(original_hash, "Admin12345!")


# ---------------------------------------------------------------- authorization


def test_operator_cannot_create_devices(client) -> None:
    test_client, manager = client
    make_user(manager, "operator2")
    login(test_client, "operator2", "Password123")

    response = test_client.post(
        "/api/devices",
        json={"name": "Router-1", "ip_address": "192.168.1.1", "csrf_token": csrf(test_client)},
    )

    assert response.status_code == 403


def test_demoting_an_admin_revokes_access_immediately(client) -> None:
    """Regression: admin_required trusted the role stamped into the session at login."""
    test_client, manager = client
    keeper = make_user(manager, "keeper", role="admin")
    demoted = make_user(manager, "demoted", role="admin")
    login(test_client, "demoted", "Password123")

    assert test_client.get("/api/users").status_code == 200

    manager.update_user_role(demoted["id"], "viewer")

    assert test_client.get("/api/users").status_code == 403
    assert keeper["role"] == "admin"


def test_state_changing_request_without_csrf_token_is_rejected(client) -> None:
    test_client, manager = client
    make_user(manager, "admin2", role="admin")
    login(test_client, "admin2", "Password123")

    response = test_client.post("/api/groups", json={"name": "no-token"})

    assert response.status_code == 403


def test_admin_can_manage_network_gateway(client) -> None:
    test_client, manager = client
    make_user(manager, "admin3", role="admin")
    login(test_client, "admin3", "Password123")

    response = test_client.post(
        "/api/settings/network-gateway",
        json={"gateway_ip": "192.168.1.254", "csrf_token": csrf(test_client)},
    )
    assert response.status_code == 200
    assert response.get_json()["gateway_ip"] == "192.168.1.254"

    # Persisted in the database, so it survives a new session.
    assert manager.get_setting("network_gateway") == "192.168.1.254"

    response = test_client.get("/api/settings/network-gateway")
    assert response.status_code == 200
    assert response.get_json()["gateway_ip"] == "192.168.1.254"


def test_last_administrator_cannot_be_demoted(client) -> None:
    _, manager = client
    admin = manager.get_user_by_username("admin")

    with pytest.raises(ValueError, match="last administrator"):
        manager.update_user_role(admin["id"], "viewer")


def test_role_must_be_a_known_value(client) -> None:
    _, manager = client
    user = make_user(manager, "operator3")

    with pytest.raises(ValueError, match="Role must be one of"):
        manager.update_user_role(user["id"], "superuser")
